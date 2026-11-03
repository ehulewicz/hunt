"""A run's answer history: observed and final rows, and the long-form prose kept from them."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from personal_os_backend.jobs_models import AutofillAnswer, AutofillObservation
from personal_os_backend.jobs_prose import stripped_spans
from personal_os_backend.jobs_questions import normalize_answer_key, upsert_question

ANSWER_SOURCES = frozenset(
    {"jobs_profile", "answer_library", "model", "pattern", "manual", "archive", "prose"}
)


async def record_observation(
    connection: AsyncConnection[Any],
    user_id: UUID,
    run_id: UUID,
    application_id: UUID,
    observation: AutofillObservation,
) -> None:
    """Record a step's observed answer as drafted history, and keep long-form writing as prose."""
    source = observation.source if observation.source in ANSWER_SOURCES else "manual"
    question_id = await upsert_question(
        connection, user_id=user_id, label=observation.question,
        form=observation.form, kind=observation.control_kind,
    )
    # A retried step re-sends the same events; the (run_id, event_index)
    # key makes the replay a no-op instead of duplicate history.
    await connection.execute(
        """
        insert into app.jobs_application_questions (
          user_id, application_id, question_text, normalized_key,
          answer_text, answer_source, answer_library_id, status,
          run_id, step_number, page_url, control_kind, field_fingerprint,
          resolver_id, proposed_value, event_index, event_kind, question_id
        ) values (
          %s, %s, %s, %s, %s, %s, null, 'drafted',
          %s, %s, %s, %s, %s, %s, %s, %s, 'observed', %s
        )
        on conflict (run_id, event_index) do nothing
        """,
        (
            user_id,
            application_id,
            observation.question,
            normalize_answer_key(observation.question),
            observation.answer_text,
            source,
            run_id,
            observation.step_number,
            observation.page_url,
            observation.control_kind,
            observation.field_fingerprint,
            observation.resolver_id,
            observation.proposed_value,
            observation.event_index,
            question_id,
        ),
    )
    await save_prose(
        connection, user_id, application_id, question_id, observation,
        submitted=False,
    )


async def record_final_answer(
    connection: AsyncConnection[Any],
    user_id: UUID,
    run_id: UUID,
    application_id: UUID,
    answer: AutofillAnswer,
    *,
    submitted: bool,
) -> None:
    """Record an answer as its run's final event, and keep long-form writing as prose."""
    question = answer.question
    answer_text = answer.answer_text.strip()
    if not question or not answer_text:
        return
    normalized_key = normalize_answer_key(question)
    source = answer.source
    question_id = await upsert_question(
        connection, user_id=user_id, label=question, form=answer.form,
        kind=answer.control_kind,
    )
    await connection.execute(
        """
        insert into app.jobs_application_questions (
          user_id, application_id, question_text, normalized_key,
          answer_text, answer_source, answer_library_id, status,
          run_id, step_number, page_url, control_kind, field_fingerprint,
          resolver_id, proposed_value, event_kind, question_id
        ) values (
          %s, %s, %s, %s, %s, %s, null, %s,
          %s, %s, %s, %s, %s, %s, %s, 'final', %s
        )
        """,
        (
            user_id,
            application_id,
            question,
            normalized_key,
            answer_text,
            source if source in ANSWER_SOURCES else "manual",
            "submitted" if submitted else "drafted",
            run_id,
            answer.step_number,
            answer.page_url,
            answer.control_kind,
            answer.field_fingerprint,
            answer.resolver_id,
            answer.proposed_value,
            question_id,
        ),
    )
    await save_prose(
        connection, user_id, application_id, question_id, answer,
        submitted=submitted,
    )


async def save_prose(
    connection: AsyncConnection[Any],
    user_id: UUID,
    application_id: UUID,
    question_id: UUID | None,
    answer: AutofillAnswer,
    *,
    submitted: bool,
) -> None:
    """Keep a long-form answer as writing, as it is written and again at the run's end.

    It waits in the Inbox until approved for reuse: one row per application and
    question, holding the latest text. Approved prose reused as it was is not
    saved again, and neither is an empty box.
    """
    text = answer.answer_text.strip()
    if answer.form != "long" or answer.source == "prose" or question_id is None or not text:
        return
    spans = stripped_spans(answer.answer_text, answer.provenance)
    prose = (
        text,
        "model" if answer.source == "model" else "manual",
        Jsonb(spans) if spans is not None else None,
        submitted,
    )
    await connection.execute(
        """
        with kept as (
          update app.jobs_prose
          set text = %s, origin = %s, provenance = %s, submitted = %s, updated_at = now()
          where user_id = %s and application_id = %s and question_id = %s and not approved
          returning id
        )
        insert into app.jobs_prose (
          user_id, question_id, application_id, text, approved, origin, provenance, submitted
        )
        select %s, %s, %s, %s, false, %s, %s, %s
        where not exists (select 1 from kept)
        """,
        (
            *prose, user_id, application_id, question_id,
            user_id, question_id, application_id, *prose,
        ),
    )
