"""A run's answer history: observed and final rows."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from personal_os_backend.jobs_models import AutofillAnswer, AutofillObservation
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
    """Record a step's observed answer as drafted history."""
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


async def record_final_answer(
    connection: AsyncConnection[Any],
    user_id: UUID,
    run_id: UUID,
    application_id: UUID,
    answer: AutofillAnswer,
    *,
    submitted: bool,
) -> None:
    """Record an answer as its run's final event."""
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
