"""Autofill runs: a run created with its context, its steps, its completion, and stopping it."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from personal_os_backend.config import StoreBase
from personal_os_backend.jobs_answer_history import (
    record_final_answer,
    record_observation,
)
from personal_os_backend.jobs_failures import record_step_failures
from personal_os_backend.jobs_gaps import stored_prose
from personal_os_backend.jobs_models import (
    AutofillFailureIssue,
    AutofillRunComplete,
    AutofillRunContext,
    AutofillRunCreate,
    AutofillRunFinish,
    AutofillRunStop,
    AutofillStepCreate,
)
from personal_os_backend.jobs_patterns import active_patterns
from personal_os_backend.jobs_registry import record_fill_events


class CompletionConflict(RuntimeError):
    """Application lifecycle changed while the autofill run was active."""


class AutofillRunsStore(Protocol):
    async def create_autofill_run(
        self, user_id: UUID, item: AutofillRunCreate
    ) -> AutofillRunContext | None: ...

    async def persist_autofill_step(
        self, user_id: UUID, run_id: UUID, item: AutofillStepCreate
    ) -> list[AutofillFailureIssue] | None: ...

    async def complete_autofill_run(
        self, user_id: UUID, run_id: UUID, item: AutofillRunComplete
    ) -> bool: ...

    async def owns_autofill_run(self, user_id: UUID, run_id: UUID) -> bool: ...

    async def stop_autofill_run(
        self, user_id: UUID, run_id: UUID, item: AutofillRunStop
    ) -> bool: ...


class AutofillRunsMixin(StoreBase):
    async def create_autofill_run(
        self, user_id: UUID, item: AutofillRunCreate
    ) -> AutofillRunContext | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                with target as (
                  select posting.id, posting.application_url, posting.discovery_url,
                         posting.description_text, posting.title, posting.company_id,
                         company.name as company_name
                  from app.jobs_postings posting
                  join app.jobs_companies company on company.id = posting.company_id
                  where posting.id = %s and posting.user_id = %s
                    and posting.application_url is not null
                    and posting.status not in ('closed', 'archived')
                ), application as (
                  insert into app.jobs_applications (user_id, posting_id, status)
                  select %s, id, 'applying' from target
                  on conflict (user_id, posting_id) do update set
                    updated_at = now(),
                    status = case when app.jobs_applications.status = 'archived'
                                  then 'applying' else app.jobs_applications.status end
                  returning id, posting_id
                ), run as (
                  insert into app.jobs_autofill_runs (
                    user_id, application_id, ats, adapter_version, application_url
                  )
                  select %s, application.id,
                         case when %s <> 'generic' then %s
                              when target.application_url ilike '%%greenhouse%%' then 'greenhouse'
                              when target.application_url ilike '%%ashbyhq%%' then 'ashby'
                              when target.application_url ilike '%%lever.co%%' then 'lever'
                              when target.application_url ilike '%%myworkday%%' then 'workday'
                              else 'generic' end,
                         %s, target.application_url
                  from application join target on target.id = application.posting_id
                  returning id, application_id, ats
                )
                select run.id as run_id, run.application_id, run.ats,
                       target.application_url,
                       target.discovery_url, target.company_name, target.title,
                       target.description_text, target.company_id
                from run join target on true
                """,
                (
                    item.posting_id,
                    user_id,
                    user_id,
                    user_id,
                    item.ats,
                    item.ats,
                    item.adapter_version,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            facts, duplicates = await _facts_and_duplicates(
                connection, user_id, row["company_id"], row["application_id"]
            )
            preferences = await _control_preferences(connection, user_id)
            patterns = await active_patterns(connection, user_id)
            # Every stored prose row rides along as reference for the fallback
            # drafter; nothing fills a field from it as is.
            prose = await stored_prose(connection, user_id)
        return AutofillRunContext(
            **{key: value for key, value in row.items() if key != "company_id"},
            facts=[dict(fact) for fact in facts],
            patterns=[pattern.model_dump(mode="json") for pattern in patterns],
            prose=[item.model_dump(mode="json") for item in prose],
            control_preferences=preferences,
            duplicate_company_applications=[dict(item) for item in duplicates],
        )

    async def persist_autofill_step(
        self, user_id: UUID, run_id: UUID, item: AutofillStepCreate
    ) -> list[AutofillFailureIssue] | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            run = await _advance_run(connection, user_id, run_id, item)
            if run is None:
                return None
            issues = await record_step_failures(
                connection, user_id, run_id, item.step_number, item.failures,
                ats=run["ats"], adapter_version=run["adapter_version"],
            )
            for observation in item.observations:
                await record_observation(
                    connection, user_id, run_id, run["application_id"], observation
                )
            await record_fill_events(
                connection,
                user_id=user_id,
                run_id=run_id,
                step=item.step_number,
                ats=run["ats"],
                events=item.fill_events,
            )
        return issues

    async def complete_autofill_run(
        self, user_id: UUID, run_id: UUID, item: AutofillRunComplete
    ) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_autofill_runs
                set status = 'review_ready', final_url = %s, completed_at = now()
                where id = %s and user_id = %s and status = 'running'
                returning application_id
                """,
                (item.final_url, run_id, user_id),
            )
            run = await cursor.fetchone()
            if run is None:
                return False
            if isinstance(item, AutofillRunFinish):
                await _finish_application(connection, user_id, run["application_id"], item)
            for answer in item.answers:
                await record_final_answer(
                    connection, user_id, run_id, run["application_id"], answer,
                    submitted=item.submitted,
                )
        return True

    async def owns_autofill_run(self, user_id: UUID, run_id: UUID) -> bool:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "select 1 from app.jobs_autofill_runs where id = %s and user_id = %s",
                (run_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def stop_autofill_run(
        self, user_id: UUID, run_id: UUID, item: AutofillRunStop
    ) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                with stopped as (
                  update app.jobs_autofill_runs
                  set status = %s, completed_at = now()
                  where id = %s and user_id = %s and status = 'running'
                  returning id, application_id
                )
                , archived as (
                  update app.jobs_applications application
                  set status = 'archived', updated_at = now()
                  from stopped
                  where application.id = stopped.application_id
                    and application.status = 'applying'
                  returning application.id
                )
                select id from stopped
                """,
                (item.status, run_id, user_id),
            )
            return await cursor.fetchone() is not None


async def _advance_run(
    connection: AsyncConnection[Any], user_id: UUID, run_id: UUID, item: AutofillStepCreate
) -> Any:
    """Fold the step into its running run; the run's row, or None when no run is running."""
    run_cursor = await connection.execute(
        """
        update app.jobs_autofill_runs
        set current_step = greatest(current_step, %s), final_url = %s,
            filled_count = filled_count + %s,
            skipped_count = skipped_count + %s,
            failure_count = failure_count + %s,
            model_calls = greatest(model_calls, coalesce(%s, 0)),
            model_usage = case
                when %s::jsonb != '{}'::jsonb then %s::jsonb
                else model_usage end
        where id = %s and user_id = %s and status = 'running'
        returning application_id, ats, adapter_version
        """,
        (
            item.step_number,
            item.url,
            item.filled_count,
            item.skipped_count,
            len(item.failures),
            item.model_calls,
            Jsonb(item.model_usage),
            Jsonb(item.model_usage),
            run_id,
            user_id,
        ),
    )
    return await run_cursor.fetchone()


async def _facts_and_duplicates(
    connection: AsyncConnection[Any], user_id: UUID, company_id: UUID, application_id: UUID
) -> tuple[list[Any], list[Any]]:
    """The user's facts, and their other live applications at the same company."""
    facts_cursor = await connection.execute(
        "select key, value, value_type from app.jobs_facts "
        "where user_id = %s order by key",
        (user_id,),
    )
    duplicate_cursor = await connection.execute(
        """
        select application.id, application.status, posting.title,
               application.created_at
        from app.jobs_applications application
        join app.jobs_postings posting on posting.id = application.posting_id
        where application.user_id = %s and posting.company_id = %s
          and application.id <> %s and application.status <> 'archived'
        order by application.created_at desc
        """,
        (user_id, company_id, application_id),
    )
    facts = await facts_cursor.fetchall()
    duplicates = await duplicate_cursor.fetchall()
    return facts, duplicates


async def _control_preferences(
    connection: AsyncConnection[Any], user_id: UUID
) -> list[dict[str, Any]]:
    """The strategy candidates you validated, oldest first, as control preferences."""
    preferences_cursor = await connection.execute(
        """
        select candidate from app.jobs_autofill_failure_issues
        where user_id = %s and status = 'validated'
          and candidate ->> 'kind' = 'strategy'
        order by validated_at
        """,
        (user_id,),
    )
    return [dict(row["candidate"]) for row in await preferences_cursor.fetchall()]


async def _finish_application(
    connection: AsyncConnection[Any], user_id: UUID, application_id: UUID, item: AutofillRunFinish
) -> None:
    """Finish the application at the run's status; a concurrent change raises CompletionConflict."""
    lifecycle = await connection.execute(
        """
        update app.jobs_applications
        set status = %s,
            applied_at = case when %s = 'applied'
                              then coalesce(applied_at, now()) else applied_at end,
            next_action = case when %s = 'archived' then null else next_action end,
            next_action_at = case when %s = 'archived'
                                  then null else next_action_at end
        where id = %s and user_id = %s and status in ('applying', %s)
        returning id
        """,
        (item.application_status, item.application_status,
         item.application_status, item.application_status,
         application_id, user_id, item.application_status),
    )
    if await lifecycle.fetchone() is None:
        raise CompletionConflict("Application status changed; completion rolled back")
