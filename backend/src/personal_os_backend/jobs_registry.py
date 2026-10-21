"""Registry instrumentation: controls, strategies, and fill events.

Fill events arrive with an autofill step and are written in the same
transaction. A control is identified by its kind and structural attributes,
never by the ATS or the question, so observations of one widget accumulate
across companies. Rates are queries over `jobs_fill_events`; nothing here keeps
a counter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_os_backend.jobs_answers import normalize_answer_key, upsert_question
from personal_os_backend.jobs_changes import ReplayKnowledge

ControlKind = Literal[
    "text_entry",
    "native_select",
    "choice_group",
    "multi_choice_group",
    "toggle",
    "dynamic_choice",
    "file",
]
StoppedAt = Literal["pattern", "format", "strategy"]
STRATEGY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# A strategy the worker reports that the seed did not know sorts after every
# seeded one until its precedence is set deliberately.
UNSEEDED_PRECEDENCE = 100


def normalize_question_key(value: str) -> str:
    """The same normalization `jobs_questions` uses for its keys."""
    return normalize_answer_key(value)


class FillEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    kind: ControlKind
    attributes: dict[str, Any] = Field(max_length=40)
    strategy: str | None = Field(default=None, max_length=80)
    resolver_id: str | None = Field(default=None, max_length=200)
    fact_key: str | None = Field(default=None, max_length=200)
    formatter: str | None = Field(default=None, max_length=80)
    source: str | None = Field(default=None, max_length=40)
    value_hash: str | None = Field(default=None, max_length=128)
    stopped_at: StoppedAt | None = None
    operated_ok: bool | None = None
    # The active pattern that produced the value, and whether the question wants prose.
    pattern_id: UUID | None = None
    form: Literal["short", "long"] | None = None
    # What the control offered, as the page showed it; None when it offered nothing.
    options: list[Annotated[str, Field(max_length=1000)]] | None = Field(
        default=None, max_length=1000
    )
    knowledge: ReplayKnowledge | None = None

    @field_validator("strategy")
    @classmethod
    def strategy_is_a_name(cls, value: str | None) -> str | None:
        if value is not None and not STRATEGY_NAME.fullmatch(value):
            raise ValueError("strategy must be a lowercase snake_case name")
        return value

    @field_validator("operated_ok")
    @classmethod
    def stopped_attempts_did_not_succeed(cls, value: bool | None, info: Any) -> bool | None:
        if value is True and info.data.get("stopped_at") is not None:
            raise ValueError("an attempt that stopped cannot have operated")
        return value


async def record_fill_events(
    connection: AsyncConnection[Any],
    *,
    user_id: UUID,
    run_id: UUID,
    step: int,
    ats: str,
    events: Sequence[FillEventCreate],
) -> int:
    """Write one row per attempt; return how many were written.

    Each event records its question (R44). Nothing here proposes or activates a
    pattern: a model never fills, and what it teaches the worker saves (R67).
    """
    controls: dict[tuple[str, str], UUID] = {}
    strategies: dict[tuple[str, str], UUID] = {}
    questions: dict[str, UUID | None] = {}
    written = 0
    for event in events:
        question_key = normalize_question_key(event.question)
        if not question_key:
            continue
        if question_key not in questions or event.form == "long":
            questions[question_key] = await upsert_question(
                connection, user_id=user_id, label=event.question, form=event.form,
                kind=event.kind,
            )
        question_id = questions[question_key]
        attributes = Jsonb(event.attributes)
        control_key = (event.kind, repr(sorted(event.attributes.items())))
        if control_key not in controls:
            cursor = await connection.execute(
                """
                with inserted as (
                  insert into app.jobs_controls (user_id, kind, attributes)
                  values (%s, %s, %s)
                  on conflict (user_id, kind, attributes) do nothing
                  returning id
                )
                select id from inserted
                union all
                select id from app.jobs_controls
                where user_id = %s and kind = %s and attributes = %s
                limit 1
                """,
                (user_id, event.kind, attributes, user_id, event.kind, attributes),
            )
            row = await cursor.fetchone()
            if row is None:
                continue
            controls[control_key] = row["id"]
        strategy_id: UUID | None = None
        if event.strategy is not None:
            strategy_key = (event.strategy, event.kind)
            if strategy_key not in strategies:
                cursor = await connection.execute(
                    """
                    with inserted as (
                      insert into app.jobs_strategies (user_id, name, control_kind, precedence)
                      values (%s, %s, %s, %s)
                      on conflict (user_id, name, control_kind) do nothing
                      returning id
                    )
                    select id from inserted
                    union all
                    select id from app.jobs_strategies
                    where user_id = %s and name = %s and control_kind = %s
                    limit 1
                    """,
                    (
                        user_id, event.strategy, event.kind, UNSEEDED_PRECEDENCE,
                        user_id, event.strategy, event.kind,
                    ),
                )
                row = await cursor.fetchone()
                if row is not None:
                    strategies[strategy_key] = row["id"]
            strategy_id = strategies.get(strategy_key)
        await connection.execute(
            """
            insert into app.jobs_fill_events (
              user_id, run_id, step, question_key, resolver_id, fact_key, formatter,
              source, control_id, strategy_id, ats, value_hash, stopped_at, operated_ok,
              question_id, pattern_id, options, knowledge
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              (select id from app.jobs_patterns where id = %s and user_id = %s), %s, %s
            )
            """,
            (
                user_id,
                run_id,
                step,
                question_key,
                event.resolver_id,
                event.fact_key,
                event.formatter,
                event.source,
                controls[control_key],
                strategy_id,
                ats,
                event.value_hash,
                event.stopped_at,
                event.operated_ok,
                question_id,
                event.pattern_id,
                user_id,
                Jsonb(event.options) if event.options is not None else None,
                Jsonb(event.knowledge.model_dump()) if event.knowledge is not None else None,
            ),
        )
        written += 1
    return written
