"""Failure issues and their occurrences: the repair queue, sites, and validation targets."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb

from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_changes import Failure, record_change
from personal_os_backend.jobs_models import (
    AutofillFailureDetail,
    AutofillFailureIssue,
    AutofillFailureUpdate,
    AutofillSite,
    ValidationTarget,
)
from personal_os_backend.jobs_questions import normalize_answer_key


class FailuresStore(Protocol):
    async def list_autofill_failures(self, user_id: UUID) -> list[AutofillFailureIssue]: ...

    async def list_autofill_sites(self, user_id: UUID) -> list[AutofillSite]: ...

    async def get_autofill_failure(
        self, user_id: UUID, issue_id: UUID
    ) -> AutofillFailureDetail | None: ...

    async def autofill_artifact_belongs_to_issue(
        self, user_id: UUID, issue_id: UUID, path: str
    ) -> bool: ...

    async def update_autofill_failure(
        self, user_id: UUID, issue_id: UUID, item: AutofillFailureUpdate
    ) -> AutofillFailureIssue | None: ...

    async def validation_target(
        self, user_id: UUID, issue_id: UUID
    ) -> ValidationTarget | None: ...


class FailuresMixin(StoreBase):
    async def list_autofill_failures(self, user_id: UUID) -> list[AutofillFailureIssue]:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, signature, ats, category, field_label, control_kind,
                       status, diagnosis, proposed_patch, fixed_commit,
                       verification_notes, occurrence_count, first_seen_at,
                       last_seen_at, updated_at, stopped_at, candidate,
                       repaired_at, validated_at
                from app.jobs_autofill_failure_issues where user_id = %s
                order by last_seen_at desc, id
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [AutofillFailureIssue.model_validate(row) for row in rows]

    async def list_autofill_sites(self, user_id: UUID) -> list[AutofillSite]:
        """Each site's latest run, newest first, with the failure issues it hit that
        are still open or unsolved."""
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select run.id, run.status, run.model_calls, run.created_at,
                       coalesce(nullif(run.final_url, ''), run.application_url) as url,
                       count(distinct issue.id)
                         filter (where issue.status in ('open', 'fixing')) as open_issues,
                       count(distinct issue.id)
                         filter (where issue.status = 'unsolved') as unsolved_issues
                from app.jobs_autofill_runs run
                left join app.jobs_autofill_failure_occurrences occurrence
                  on occurrence.run_id = run.id and occurrence.user_id = run.user_id
                left join app.jobs_autofill_failure_issues issue
                  on issue.id = occurrence.issue_id and issue.user_id = run.user_id
                where run.user_id = %s
                group by run.id
                order by run.created_at desc, run.id
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return latest_per_site(rows)

    async def get_autofill_failure(
        self, user_id: UUID, issue_id: UUID
    ) -> AutofillFailureDetail | None:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, signature, ats, category, field_label, control_kind,
                       status, diagnosis, proposed_patch, fixed_commit,
                       verification_notes, occurrence_count, first_seen_at,
                       last_seen_at, updated_at, stopped_at, candidate,
                       repaired_at, validated_at
                from app.jobs_autofill_failure_issues where user_id = %s and id = %s
                """,
                (user_id, issue_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            occurrence_cursor = await connection.execute(
                """
                select occurrence.id, occurrence.run_id, occurrence.step_number,
                       occurrence.field_fingerprint, occurrence.intended_value,
                       occurrence.observed_value, occurrence.error_message,
                       occurrence.dom_signals, occurrence.artifact_paths,
                       occurrence.adapter_version, occurrence.created_at,
                       run.application_url
                from app.jobs_autofill_failure_occurrences occurrence
                left join app.jobs_autofill_runs run on run.id = occurrence.run_id
                where occurrence.user_id = %s and occurrence.issue_id = %s
                order by occurrence.created_at desc limit 20
                """,
                (user_id, issue_id),
            )
            occurrences = await occurrence_cursor.fetchall()
        return AutofillFailureDetail.model_validate(
            {**dict(row), "occurrences": [dict(item) for item in occurrences]}
        )

    async def autofill_artifact_belongs_to_issue(
        self, user_id: UUID, issue_id: UUID, path: str
    ) -> bool:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select 1 from app.jobs_autofill_failure_occurrences occurrence
                where occurrence.user_id = %s and occurrence.issue_id = %s
                  and exists (
                    select 1 from jsonb_each_text(occurrence.artifact_paths) artifact
                    where artifact.value = %s
                  )
                limit 1
                """,
                (user_id, issue_id, path),
            )
            return await cursor.fetchone() is not None

    async def update_autofill_failure(
        self, user_id: UUID, issue_id: UUID, item: AutofillFailureUpdate
    ) -> AutofillFailureIssue | None:
        values = item.model_dump(exclude_unset=True)
        if not values:
            return await self.get_autofill_failure(user_id, issue_id)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            current_cursor = await connection.execute(
                """
                select status, candidate from app.jobs_autofill_failure_issues
                where user_id = %s and id = %s for update
                """,
                (user_id, issue_id),
            )
            current = await current_cursor.fetchone()
            if current is None:
                return None
            next_status = values.get("status")
            _check_transition(current, next_status)
            if next_status == "fixed" and not values.get("fixed_commit"):
                raise ValueError("A fixed issue requires the verified Git commit")
            if next_status == "repaired" and not (values.get("candidate") or current["candidate"]):
                raise ValueError("A repaired issue carries the rule that repaired it")
            if next_status == "fixed":
                # A fix is kept only once it read back on the live control.
                values["live_verified_at"] = datetime.now(UTC)
            if next_status == "repaired":
                values["repaired_at"] = datetime.now(UTC)
            if next_status == "validated":
                values["validated_at"] = datetime.now(UTC)
            if next_status == "open" and current["status"] == "repaired":
                values["candidate"] = None
            if "candidate" in values and values["candidate"] is not None:
                values["candidate"] = Jsonb(values["candidate"])
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values
            )
            cursor = await connection.execute(
                sql.SQL(
                    """
                    update app.jobs_autofill_failure_issues set {}
                    where user_id = %s and id = %s
                    returning id, signature, ats, category, field_label, control_kind,
                              status, diagnosis, proposed_patch, fixed_commit,
                              verification_notes, occurrence_count, first_seen_at,
                              last_seen_at, updated_at, stopped_at, candidate,
                              repaired_at, validated_at
                    """
                ).format(assignments),
                (*values.values(), user_id, issue_id),
            )
            row = await cursor.fetchone()
            candidate = (row or {}).get("candidate") or {}
            validated = row is not None and next_status == "validated"
            if validated and candidate.get("kind") == "strategy":
                # Watching it work makes it that control's first strategy from now on.
                await record_change(
                    connection, user_id, subject="strategy", subject_id=str(row["signature"]),
                    action="create", before=None, after=candidate, origin="you",
                    failure=Failure(issue_id=issue_id),
                )
        return AutofillFailureIssue.model_validate(row) if row else None

    async def validation_target(
        self, user_id: UUID, issue_id: UUID
    ) -> ValidationTarget | None:
        """An unsubmitted application that showed the failed question (R26).

        Another posting first, so validating never touches the application the
        failure happened on unless nothing else shows the question; never one
        that went in, and never a closed posting.
        """
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select issue.field_label, (
                  select run.application_id
                  from app.jobs_autofill_failure_occurrences occurrence
                  join app.jobs_autofill_runs run on run.id = occurrence.run_id
                  where occurrence.issue_id = issue.id
                  order by occurrence.created_at desc limit 1
                ) as application_id
                from app.jobs_autofill_failure_issues issue
                where issue.user_id = %s and issue.id = %s
                """,
                (user_id, issue_id),
            )
            issue = await cursor.fetchone()
            if issue is None or not issue["field_label"]:
                return None
            cursor = await connection.execute(
                """
                select posting.application_url, company.name as company_name, posting.title,
                       application.id is not distinct from %s as original
                from app.jobs_applications application
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                where application.user_id = %s
                  and application.status in ('applying', 'archived', 'skipped')
                  and posting.application_url is not null and posting.status <> 'closed'
                  and (
                    application.id = %s
                    or exists (
                      select 1 from app.jobs_fill_events event
                      join app.jobs_autofill_runs run on run.id = event.run_id
                      where run.application_id = application.id and event.user_id = %s
                        and event.question_key = %s
                    )
                  )
                order by application.id is not distinct from %s, application.updated_at desc
                limit 1
                """,
                (
                    issue["application_id"],
                    user_id,
                    issue["application_id"],
                    user_id,
                    normalize_answer_key(str(issue["field_label"])),
                    issue["application_id"],
                ),
            )
            row = await cursor.fetchone()
        return ValidationTarget.model_validate(row) if row else None


def _check_transition(current: dict[str, Any], next_status: str | None) -> None:
    """Refuse a status the issue cannot move to from the one it holds."""
    # The fixer takes an issue from wherever it stands; a fix it keeps
    # is fixed, one it gives up on is unsolved and can be taken again.
    allowed = {
        "open": {"open", "fixing", "fixed", "unsolved", "repaired"},
        "fixing": {"fixing", "fixed", "unsolved", "open", "repaired"},
        "unsolved": {"unsolved", "fixing", "fixed", "open", "repaired", "archived"},
        "fixed": {"fixed"},
        "archived": {"archived"},
        # A repaired rule is validated by watching it work, or rejected back.
        "repaired": {"repaired", "validated", "open", "fixing"},
        "validated": {"validated"},
    }
    if next_status is not None and next_status not in allowed[str(current["status"])]:
        raise ValueError(
            f"Invalid failure transition: {current['status']} -> {next_status}"
        )


async def record_step_failures(
    connection: AsyncConnection[Any],
    user_id: UUID,
    run_id: UUID,
    step_number: int,
    failures: list[dict[str, Any]],
    *,
    ats: str,
    adapter_version: str,
) -> list[AutofillFailureIssue]:
    """Upsert each failure's issue and record this step's occurrence of it; return the issues."""
    issues: list[AutofillFailureIssue] = []
    for failure in failures:
        signature = str(failure.get("signature") or "").strip()
        category = str(failure.get("category") or "unsupported_control").strip()
        if not signature:
            continue
        # Where the fill stopped decides whether Repair can retry it.
        stopped_at = failure.get("stopped_at")
        if stopped_at not in {"format", "strategy"}:
            stopped_at = None
        issue_cursor = await connection.execute(
            """
            insert into app.jobs_autofill_failure_issues (
              user_id, signature, ats, category, field_label, control_kind, stopped_at
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (user_id, signature) do update set
              occurrence_count = app.jobs_autofill_failure_issues.occurrence_count + 1,
              last_seen_at = now(),
              stopped_at = coalesce(
                excluded.stopped_at, app.jobs_autofill_failure_issues.stopped_at
              ),
              -- A fix that fails again did not hold: back to the queue.
              -- An unsolved issue stays unsolved until --retry or a new
              -- fixer API version, and one being worked stays with it.
              status = case
                when app.jobs_autofill_failure_issues.status in (
                  'fixed', 'archived', 'repaired', 'validated'
                ) then 'open'
                else app.jobs_autofill_failure_issues.status
              end
            returning id, signature, ats, category, field_label, control_kind,
                      status, diagnosis, proposed_patch, fixed_commit,
                      verification_notes, occurrence_count, first_seen_at,
                      last_seen_at, updated_at, stopped_at, candidate,
                      repaired_at, validated_at
            """,
            (
                user_id,
                signature,
                ats,
                category,
                failure.get("field_label"),
                failure.get("control_kind"),
                stopped_at,
            ),
        )
        issue = await issue_cursor.fetchone()
        if issue is None:
            continue
        await _record_occurrence(
            connection, user_id, issue["id"], run_id, step_number, adapter_version, failure
        )
        issues.append(AutofillFailureIssue.model_validate(issue))
    return issues


async def _record_occurrence(
    connection: AsyncConnection[Any],
    user_id: UUID,
    issue_id: UUID,
    run_id: UUID,
    step_number: int,
    adapter_version: str,
    failure: dict[str, Any],
) -> None:
    """Record one sighting of an issue: the run, the step, the values, and the evidence."""
    await connection.execute(
        """
        insert into app.jobs_autofill_failure_occurrences (
          user_id, issue_id, run_id, step_number, field_fingerprint,
          intended_value, observed_value, error_message, dom_signals,
          artifact_paths, adapter_version
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            issue_id,
            run_id,
            step_number,
            failure.get("field_fingerprint"),
            failure.get("intended_value"),
            failure.get("observed_value"),
            failure.get("error_message"),
            Jsonb(
                (failure.get("dom_signals") or {})
                | {
                    key: failure[key]
                    for key in ("fact_key", "formatter", "question")
                    if failure.get(key)
                }
            ),
            Jsonb(failure.get("artifact_paths") or {}),
            adapter_version,
        ),
    )


def registrable_site(url: str) -> str:
    """The registrable part of a URL's host, near enough: its last two labels, as the
    worker names a site."""
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return ".".join(host.split(".")[-2:])


def latest_per_site(runs: Sequence[dict[str, Any]]) -> list[AutofillSite]:
    """Runs, newest first, folded to each site's latest one and how many runs it had."""
    latest: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for run in runs:
        site = registrable_site(str(run["url"] or ""))
        if not site:
            continue
        counts[site] = counts.get(site, 0) + 1
        latest.setdefault(site, run)
    return [
        AutofillSite(
            site=site, runs=counts[site], run_id=run["id"], run_status=run["status"],
            model_calls=run["model_calls"], last_run_at=run["created_at"],
            open_issues=run["open_issues"], unsolved_issues=run["unsolved_issues"],
        )
        for site, run in latest.items()
    ]
