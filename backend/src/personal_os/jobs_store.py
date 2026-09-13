from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from fastapi import Request
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from personal_os.config import Settings
from personal_os.jobs_answers import answer_storage_key, normalize_answer_key
from personal_os.jobs_models import (
    AnswerLibraryCreate,
    AnswerLibraryItem,
    AnswerLibraryUpdate,
    ApplicationUpdate,
    AutofillFailureDetail,
    AutofillFailureIssue,
    AutofillFailureUpdate,
    AutofillRunComplete,
    AutofillRunContext,
    AutofillRunCreate,
    AutofillRunStop,
    AutofillStepCreate,
    BatchPrepareResponse,
    JobApplication,
    JobDocument,
    JobDocumentReview,
    JobListing,
    JobListingPage,
    JobsProfile,
    JobsProfileUpdate,
    ListingSyncResponse,
    MasterResumeContent,
    WorkerApplicationBundle,
    WorkerTask,
)
from personal_os.jobs_sources import SOURCE_NAME, SourceListing


class JobsStore(Protocol):
    async def get_profile(self, user_id: UUID) -> JobsProfile: ...

    async def update_profile(self, user_id: UUID, update: JobsProfileUpdate) -> JobsProfile: ...

    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse: ...

    async def list_listings(
        self,
        user_id: UUID,
        *,
        category: str | None,
        locations: Sequence[str],
        role_families: Sequence[str],
        limit: int,
        offset: int,
    ) -> JobListingPage: ...

    async def prepare_applications(
        self, user_id: UUID, posting_ids: Sequence[UUID], *, tailor: bool
    ) -> BatchPrepareResponse: ...

    async def list_applications(self, user_id: UUID) -> list[JobApplication]: ...

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None: ...

    async def archive_application(self, user_id: UUID, application_id: UUID) -> bool: ...

    async def queue_autofill(self, user_id: UUID, application_id: UUID) -> UUID | None: ...

    async def list_documents(self, user_id: UUID) -> list[JobDocument]: ...

    async def create_master_resume_draft(
        self, user_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None: ...

    async def update_master_resume_draft(
        self, user_id: UUID, document_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None: ...

    async def queue_resume_preview(self, user_id: UUID, document_id: UUID) -> UUID | None: ...

    async def get_master_resume_draft_content(
        self, user_id: UUID, document_id: UUID
    ) -> str | None: ...

    async def approve_document(self, user_id: UUID, document_id: UUID) -> JobDocument | None: ...

    async def get_document_storage_path(
        self, user_id: UUID, document_id: UUID
    ) -> tuple[str, str] | None: ...

    async def get_document_review(
        self, user_id: UUID, document_id: UUID
    ) -> JobDocumentReview | None: ...

    async def get_approved_resume_storage_path(
        self, user_id: UUID, application_id: UUID
    ) -> tuple[str, str] | None: ...

    async def list_answers(self, user_id: UUID) -> list[AnswerLibraryItem]: ...

    async def create_answer(
        self, user_id: UUID, item: AnswerLibraryCreate
    ) -> AnswerLibraryItem: ...

    async def update_answer(
        self, user_id: UUID, answer_id: UUID, item: AnswerLibraryUpdate
    ) -> AnswerLibraryItem | None: ...

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

    async def list_autofill_failures(self, user_id: UUID) -> list[AutofillFailureIssue]: ...

    async def get_autofill_failure(
        self, user_id: UUID, issue_id: UUID
    ) -> AutofillFailureDetail | None: ...

    async def autofill_artifact_belongs_to_issue(
        self, user_id: UUID, issue_id: UUID, path: str
    ) -> bool: ...

    async def update_autofill_failure(
        self, user_id: UUID, issue_id: UUID, item: AutofillFailureUpdate
    ) -> AutofillFailureIssue | None: ...

    async def claim_task(self, user_id: UUID, worker_id: str) -> WorkerTask | None: ...

    async def get_worker_application_bundle(
        self, user_id: UUID, application_id: UUID
    ) -> WorkerApplicationBundle | None: ...

    async def get_worker_task_application_id(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> UUID | None: ...

    async def get_worker_task_document_id(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> UUID | None: ...

    async def heartbeat_task(self, user_id: UUID, task_id: UUID, worker_id: str) -> bool: ...

    async def complete_task(
        self, user_id: UUID, task_id: UUID, worker_id: str, result: dict[str, Any]
    ) -> bool: ...

    async def fail_task(
        self,
        user_id: UUID,
        task_id: UUID,
        worker_id: str,
        error_message: str,
        *,
        retry: bool,
    ) -> bool: ...


PROFILE_COLUMNS = (
    "legal_name",
    "preferred_name",
    "email",
    "phone",
    "address_line_1",
    "address_line_2",
    "city",
    "region",
    "postal_code",
    "country",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "work_authorization_status",
    "visa_status",
    "sponsorship_required",
    "school_name",
    "degree_type",
    "field_of_study",
    "graduation_date",
    "gpa",
    "earliest_start_date",
    "earliest_start_text",
    "years_professional_experience",
    "remote_preference",
    "willing_to_relocate",
    "willing_to_travel",
    "target_roles",
    "target_locations",
    "target_career_stages",
    "minimum_salary",
    "professional_summary",
    "eeo_gender_identity",
    "eeo_race_ethnicities",
    "eeo_veteran_status",
    "eeo_disability_status",
    "completed_at",
    "updated_at",
)


class PostgresJobsStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def get_profile(self, user_id: UUID) -> JobsProfile:
        columns = sql.SQL(", ").join(map(sql.Identifier, PROFILE_COLUMNS))
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                sql.SQL("select {} from app.jobs_profile where user_id = %s").format(columns),
                (user_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise LookupError("Jobs profile is missing")
        return JobsProfile.model_validate(row)

    async def update_profile(self, user_id: UUID, update: JobsProfileUpdate) -> JobsProfile:
        values = update.model_dump(exclude_unset=True)
        completed = values.pop("completed", None)
        values = {
            key: str(value) if key.endswith("_url") and value is not None else value
            for key, value in values.items()
        }
        if completed is not None:
            values["completed_at"] = datetime.now(UTC) if completed else None
        if values:
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values
            )
            async with (
                self._pool.connection(timeout=self._timeout) as connection,
                connection.transaction(),
            ):
                await connection.execute("set local role personal_os_api")
                await connection.execute(
                    sql.SQL("update app.jobs_profile set {} where user_id = %s").format(
                        assignments
                    ),
                    (*values.values(), user_id),
                )
        return await self.get_profile(user_id)

    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse:
        sync_started = datetime.now(UTC)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            company_ids: dict[str, UUID] = {}
            if listings:
                companies: dict[str, str | None] = {}
                for listing in listings:
                    current_url = companies.get(listing.company)
                    companies[listing.company] = current_url or listing.company_url

                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        """
                        insert into app.jobs_companies (user_id, name, website_url)
                        values (%s, %s, %s)
                        on conflict (user_id, name) do update
                        set website_url = coalesce(
                          excluded.website_url, app.jobs_companies.website_url
                        )
                        """,
                        [
                            (user_id, company_name, company_url)
                            for company_name, company_url in companies.items()
                        ],
                    )
                company_cursor = await connection.execute(
                    """
                    select id, name
                    from app.jobs_companies
                    where user_id = %s and name = any(%s::text[])
                    """,
                    (user_id, list(companies)),
                )
                company_ids = {
                    str(row["name"]): UUID(str(row["id"]))
                    for row in await company_cursor.fetchall()
                }
                if company_ids.keys() != companies.keys():
                    raise RuntimeError("Company upsert did not return every id")

                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        """
                        insert into app.jobs_postings (
                          user_id, company_id, title, location, employment_type,
                          source, source_url, external_id, salary_text, status,
                          discovered_at, application_url, discovery_url, role_families,
                          source_category, normalized_locations, career_stage,
                          feed_region, source_posted_at, source_age_days,
                          last_seen_at
                        )
                        values (
                          %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s,
                          %s, %s, %s, %s,
                          %s, %s, %s, %s,
                          %s, %s,
                          %s
                        )
                        on conflict (user_id, source, external_id)
                        where source is not null and external_id is not null
                        do update set
                          company_id = excluded.company_id,
                          title = excluded.title,
                          location = excluded.location,
                          employment_type = excluded.employment_type,
                          source_url = excluded.source_url,
                          salary_text = excluded.salary_text,
                          status = case
                            when app.jobs_postings.status in ('saved', 'reviewing', 'closed')
                              then excluded.status
                            else app.jobs_postings.status
                          end,
                          application_url = excluded.application_url,
                          discovery_url = excluded.discovery_url,
                          role_families = excluded.role_families,
                          source_category = excluded.source_category,
                          normalized_locations = excluded.normalized_locations,
                          career_stage = excluded.career_stage,
                          feed_region = excluded.feed_region,
                          source_posted_at = excluded.source_posted_at,
                          source_age_days = excluded.source_age_days,
                          last_seen_at = excluded.last_seen_at
                        """,
                        [
                            (
                                user_id,
                                company_ids[listing.company],
                                listing.title,
                                listing.location,
                                listing.career_stage,
                                SOURCE_NAME,
                                listing.application_url,
                                listing.external_id,
                                listing.salary_text,
                                "saved" if listing.application_url else "closed",
                                sync_started,
                                listing.application_url,
                                listing.discovery_url,
                                list(listing.role_families),
                                listing.category,
                                list(listing.normalized_locations),
                                listing.career_stage,
                                listing.feed_region,
                                listing.source_posted_at,
                                listing.source_age_days,
                                sync_started,
                            )
                            for listing in listings
                        ],
                    )

            await connection.execute(
                """
                update app.jobs_postings posting
                set status = 'closed'
                where posting.user_id = %s
                  and posting.source = %s
                  and posting.last_seen_at < %s
                  and posting.status in ('saved', 'reviewing')
                """,
                (user_id, SOURCE_NAME, sync_started),
            )
            count_cursor = await connection.execute(
                """
                select count(*) as seen,
                       count(*) filter (where application_url is not null) as open
                from app.jobs_postings
                where user_id = %s and source = %s and last_seen_at = %s
                """,
                (user_id, SOURCE_NAME, sync_started),
            )
            counts = await count_cursor.fetchone()
        return ListingSyncResponse(
            source=SOURCE_NAME,
            seen=int(counts["seen"] if counts else 0),
            open=int(counts["open"] if counts else 0),
            synced_at=sync_started,
        )

    async def list_listings(
        self,
        user_id: UUID,
        *,
        category: str | None,
        locations: Sequence[str],
        role_families: Sequence[str],
        limit: int,
        offset: int,
    ) -> JobListingPage:
        conditions = [
            sql.SQL("posting.user_id = %s"),
            sql.SQL("posting.status not in ('closed', 'archived')"),
            sql.SQL("posting.career_stage = 'internship'"),
            sql.SQL("posting.feed_region = 'usa'"),
        ]
        params: list[Any] = [user_id]
        if category:
            conditions.append(sql.SQL("posting.source_category = %s"))
            params.append(category)
        if role_families:
            conditions.append(sql.SQL("posting.role_families && %s::text[]"))
            params.append(list(role_families))
        facet_conditions = list(conditions)
        facet_params = list(params)
        if locations:
            conditions.append(sql.SQL("posting.normalized_locations && %s::text[]"))
            params.append(list(locations))
        where = sql.SQL(" and ").join(conditions)
        base = sql.SQL(
            """
            from app.jobs_postings posting
            join app.jobs_companies company on company.id = posting.company_id
            left join app.jobs_applications application
              on application.user_id = posting.user_id and application.posting_id = posting.id
             and application.status <> 'archived'
            where {}
            """
        ).format(where)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            location_cursor = await connection.execute(
                sql.SQL(
                    """
                    select distinct normalized.location as location
                    from app.jobs_postings posting
                    cross join lateral unnest(posting.normalized_locations)
                      as normalized(location)
                    where {}
                    order by normalized.location
                    """
                ).format(sql.SQL(" and ").join(facet_conditions)),
                facet_params,
            )
            location_rows = await location_cursor.fetchall()
            count_cursor = await connection.execute(
                sql.SQL("select count(*) as total {}").format(base), params
            )
            count = await count_cursor.fetchone()
            rows_cursor = await connection.execute(
                sql.SQL(
                    """
                    select posting.id, company.name as company_name, posting.title,
                           posting.location, posting.salary_text, posting.application_url,
                           posting.discovery_url, posting.role_families,
                           posting.source_category, posting.normalized_locations,
                           posting.career_stage, posting.feed_region,
                           posting.source_posted_at, posting.source_age_days,
                           posting.status, application.id as application_id
                    {}
                    order by posting.source_age_days asc nulls last,
                             posting.source_posted_at desc nulls last,
                             posting.created_at desc
                    limit %s offset %s
                    """
                ).format(base),
                (*params, limit, offset),
            )
            rows = await rows_cursor.fetchall()
        return JobListingPage(
            items=[JobListing.model_validate(row) for row in rows],
            total=int(count["total"] if count else 0),
            limit=limit,
            offset=offset,
            location_options=[str(row["location"]) for row in location_rows],
        )

    async def prepare_applications(
        self, user_id: UUID, posting_ids: Sequence[UUID], *, tailor: bool
    ) -> BatchPrepareResponse:
        application_ids: list[UUID] = []
        task_ids: list[UUID] = []
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            for posting_id in dict.fromkeys(posting_ids):
                application_cursor = await connection.execute(
                    """
                    insert into app.jobs_applications (user_id, posting_id)
                    select %s, id from app.jobs_postings
                    where id = %s and user_id = %s and application_url is not null
                    on conflict (user_id, posting_id) do update set
                      updated_at = now(), status = 'preparing', applied_at = null,
                      next_action = null, next_action_at = null
                    returning id
                    """,
                    (user_id, posting_id, user_id),
                )
                application = await application_cursor.fetchone()
                if application is None:
                    continue
                application_id = application["id"]
                application_ids.append(application_id)
                existing_cursor = await connection.execute(
                    """
                    select id from app.worker_tasks
                    where user_id = %s and domain = 'jobs'
                      and task_type = 'prepare_application'
                      and payload->>'application_id' = %s
                      and status in ('queued', 'running')
                    limit 1
                    """,
                    (user_id, str(application_id)),
                )
                existing = await existing_cursor.fetchone()
                if existing:
                    task_ids.append(existing["id"])
                    continue
                task_id = uuid4()
                await connection.execute(
                    """
                    insert into app.worker_tasks (id, user_id, domain, task_type, payload)
                    values (
                      %s, %s, 'jobs', 'prepare_application',
                      jsonb_build_object(
                        'application_id', %s::text,
                        'tailor', %s::boolean
                      )
                    )
                    """,
                    (task_id, user_id, application_id, tailor),
                )
                task_ids.append(task_id)
        return BatchPrepareResponse(
            application_ids=application_ids,
            queued_task_ids=task_ids,
        )

    async def list_applications(self, user_id: UUID) -> list[JobApplication]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select application.id, application.posting_id, company.name as company_name,
                       posting.title, posting.location, posting.application_url,
                       application.status, application.applied_at, application.next_action,
                       application.next_action_at, application.notes,
                       application.created_at, application.updated_at,
                       preparation.status as preparation_status,
                       left(preparation.error_message, 1000) as preparation_error,
                       autofill.status as autofill_status,
                       left(autofill.error_message, 1000) as autofill_error,
                       coalesce(questions.items, array[]::text[])
                         as unanswered_required_questions
                from app.jobs_applications application
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                left join lateral (
                  select status, error_message
                  from app.worker_tasks
                  where user_id = application.user_id and domain = 'jobs'
                    and task_type = 'prepare_application'
                    and payload->>'application_id' = application.id::text
                  order by created_at desc limit 1
                ) preparation on true
                left join lateral (
                  select status, error_message
                  from app.worker_tasks
                  where user_id = application.user_id and domain = 'jobs'
                    and task_type = 'autofill_application'
                    and payload->>'application_id' = application.id::text
                  order by created_at desc limit 1
                ) autofill on true
                left join lateral (
                  select array_agg(question_text order by display_order, created_at) as items
                  from app.jobs_application_questions
                  where user_id = application.user_id
                    and application_id = application.id and status = 'unanswered'
                ) questions on true
                where application.user_id = %s and application.status <> 'archived'
                order by application.updated_at desc
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [JobApplication.model_validate(row) for row in rows]

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None:
        values = update.model_dump(exclude_unset=True)
        if values.get("status") == "submitted":
            values["applied_at"] = datetime.now(UTC)
        if values:
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values
            )
            async with (
                self._pool.connection(timeout=self._timeout) as connection,
                connection.transaction(),
            ):
                await connection.execute("set local role personal_os_api")
                await connection.execute(
                    sql.SQL(
                        "update app.jobs_applications set {} where id = %s and user_id = %s"
                    ).format(assignments),
                    (*values.values(), application_id, user_id),
                )
        applications = await self.list_applications(user_id)
        return next((item for item in applications if item.id == application_id), None)

    async def archive_application(self, user_id: UUID, application_id: UUID) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            application_cursor = await connection.execute(
                """
                select id from app.jobs_applications
                where id = %s and user_id = %s and status <> 'archived'
                for update
                """,
                (application_id, user_id),
            )
            if await application_cursor.fetchone() is None:
                return False
            await connection.execute(
                """
                update app.worker_tasks
                set status = 'cancelled', completed_at = now(), lease_expires_at = null,
                    error_message = 'Application archived'
                where user_id = %s and domain = 'jobs'
                  and task_type in ('prepare_application', 'autofill_application')
                  and payload->>'application_id' = %s
                  and status in ('queued', 'running')
                """,
                (user_id, str(application_id)),
            )
            cursor = await connection.execute(
                """
                update app.jobs_applications
                set status = 'archived', next_action = null, next_action_at = null
                where id = %s and user_id = %s and status <> 'archived'
                """,
                (application_id, user_id),
            )
            if int(cursor.rowcount) != 1:
                return False
        return True

    async def queue_autofill(self, user_id: UUID, application_id: UUID) -> UUID | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            eligible_cursor = await connection.execute(
                """
                select application.id
                from app.jobs_applications application
                where application.id = %s and application.user_id = %s
                  and application.status = 'ready'
                  and exists (
                    select 1 from app.jobs_documents document
                    where document.user_id = application.user_id
                      and document.application_id = application.id
                      and document.document_type = 'tailored_resume'
                      and document.status = 'approved' and document.is_current
                      and document.storage_path is not null
                  )
                for update
                """,
                (application_id, user_id),
            )
            if await eligible_cursor.fetchone() is None:
                return None
            task_id = uuid4()
            task_cursor = await connection.execute(
                """
                insert into app.worker_tasks (id, user_id, domain, task_type, payload)
                values (
                  %s, %s, 'jobs', 'autofill_application',
                  jsonb_build_object('application_id', %s::text)
                )
                on conflict (user_id, (payload ->> 'application_id'))
                where domain = 'jobs'
                  and task_type = 'autofill_application'
                  and status in ('queued', 'running')
                do nothing
                returning id
                """,
                (task_id, user_id, application_id),
            )
            inserted = await task_cursor.fetchone()
            if inserted is not None:
                return UUID(str(inserted["id"]))
            existing_cursor = await connection.execute(
                """
                select id from app.worker_tasks
                where user_id = %s and domain = 'jobs'
                  and task_type = 'autofill_application'
                  and payload->>'application_id' = %s
                  and status in ('queued', 'running')
                order by created_at desc limit 1
                """,
                (user_id, str(application_id)),
            )
            existing = await existing_cursor.fetchone()
            return UUID(str(existing["id"])) if existing is not None else None

    async def list_documents(self, user_id: UUID) -> list[JobDocument]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, application_id, document_type, title, family_id, version,
                       source_document_id, content_text, storage_path, mime_type,
                       status, is_current, created_at, updated_at
                from app.jobs_documents
                where user_id = %s
                order by document_type, created_at desc
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [JobDocument.model_validate(row) for row in rows]

    async def create_master_resume_draft(
        self, user_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            family_cursor = await connection.execute(
                """
                select family_id, max(version) as version
                from app.jobs_documents
                where user_id = %s and document_type = 'master_resume'
                group by family_id order by max(version) desc limit 1
                """,
                (user_id,),
            )
            family = await family_cursor.fetchone()
            if family is None:
                return None
            cursor = await connection.execute(
                """
                insert into app.jobs_documents (
                  user_id, document_type, title, family_id, version,
                  content_text, mime_type, status, is_current
                ) values (
                  %s, 'master_resume', 'Main resume', %s, %s,
                  %s, 'application/json', 'draft', false
                )
                returning id, application_id, document_type, title, family_id, version,
                          source_document_id, content_text, storage_path, mime_type,
                          status, is_current, created_at, updated_at
                """,
                (
                    user_id,
                    family["family_id"],
                    int(family["version"]) + 1,
                    content.model_dump_json(),
                ),
            )
            row = await cursor.fetchone()
        return JobDocument.model_validate(row) if row is not None else None

    async def update_master_resume_draft(
        self, user_id: UUID, document_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None:
        """Drafts stay mutable; only approved documents are frozen by the trigger."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_documents
                set content_text = %s, storage_path = null, updated_at = now()
                where id = %s and user_id = %s
                  and document_type = 'master_resume' and status = 'draft'
                returning id, application_id, document_type, title, family_id, version,
                          source_document_id, content_text, storage_path, mime_type,
                          status, is_current, created_at, updated_at
                """,
                (content.model_dump_json(), document_id, user_id),
            )
            row = await cursor.fetchone()
        return JobDocument.model_validate(row) if row is not None else None

    async def queue_resume_preview(self, user_id: UUID, document_id: UUID) -> UUID | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            document_cursor = await connection.execute(
                """
                select id from app.jobs_documents
                where id = %s and user_id = %s
                  and document_type = 'master_resume' and status = 'draft'
                for update
                """,
                (document_id, user_id),
            )
            if await document_cursor.fetchone() is None:
                return None
            existing_cursor = await connection.execute(
                """
                select id from app.worker_tasks
                where user_id = %s and domain = 'jobs' and task_type = 'preview_resume'
                  and status in ('queued', 'running')
                  and payload->>'document_id' = %s
                """,
                (user_id, str(document_id)),
            )
            existing = await existing_cursor.fetchone()
            if existing is not None:
                return UUID(str(existing["id"]))
            task_id = uuid4()
            await connection.execute(
                """
                insert into app.worker_tasks (id, user_id, domain, task_type, payload)
                values (
                  %s, %s, 'jobs', 'preview_resume',
                  jsonb_build_object('document_id', %s::text)
                )
                """,
                (task_id, user_id, document_id),
            )
        return task_id

    async def get_master_resume_draft_content(self, user_id: UUID, document_id: UUID) -> str | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select content_text from app.jobs_documents
                where id = %s and user_id = %s and document_type = 'master_resume'
                """,
                (document_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None or not row["content_text"]:
            return None
        return str(row["content_text"])

    async def approve_document(self, user_id: UUID, document_id: UUID) -> JobDocument | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            selected_cursor = await connection.execute(
                """
                select family_id, document_type, storage_path, application_id
                from app.jobs_documents
                where id = %s and user_id = %s and status = 'draft'
                for update
                """,
                (document_id, user_id),
            )
            selected = await selected_cursor.fetchone()
            if selected is None:
                return None
            if (
                selected["document_type"] in {"tailored_resume", "cover_letter"}
                and not selected["storage_path"]
            ):
                return None
            await connection.execute(
                """
                update app.jobs_documents
                set is_current = false
                where user_id = %s and family_id = %s and is_current and id <> %s
                """,
                (user_id, selected["family_id"], document_id),
            )
            await connection.execute(
                """
                update app.jobs_documents set status = 'approved', is_current = true
                where id = %s and user_id = %s
                """,
                (document_id, user_id),
            )
            application_cursor = await connection.execute(
                """
                update app.jobs_applications application
                set status = 'ready'
                from app.jobs_documents document
                where document.id = %s and document.user_id = %s
                  and document.document_type = 'tailored_resume'
                  and application.id = document.application_id
                  and application.status in ('preparing', 'ready')
                returning application.id
                """,
                (document_id, user_id),
            )
            if (
                int(application_cursor.rowcount) == 1
                and selected["document_type"] == "tailored_resume"
                and selected["application_id"]
            ):
                application_id = UUID(str(selected["application_id"]))
                await connection.execute(
                    """
                    insert into app.worker_tasks (user_id, domain, task_type, payload)
                    values (
                      %s, 'jobs', 'autofill_application',
                      jsonb_build_object('application_id', %s::text)
                    )
                    on conflict (user_id, (payload ->> 'application_id'))
                    where domain = 'jobs'
                      and task_type = 'autofill_application'
                      and status in ('queued', 'running')
                    do nothing
                    """,
                    (user_id, application_id),
                )
        documents = await self.list_documents(user_id)
        return next((item for item in documents if item.id == document_id), None)

    async def get_document_storage_path(
        self, user_id: UUID, document_id: UUID
    ) -> tuple[str, str] | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select storage_path, title from app.jobs_documents
                where id = %s and user_id = %s and storage_path is not null
                """,
                (document_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return str(row["storage_path"]), str(row["title"])

    async def get_document_review(
        self, user_id: UUID, document_id: UUID
    ) -> JobDocumentReview | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select to_jsonb(document) - 'user_id' as document,
                       to_jsonb(source) - 'user_id' as source_document,
                       run.changed_word_ratio,
                       coalesce(
                         case
                           when jsonb_typeof(run.diff_json->'patch') = 'array'
                             then run.diff_json->'patch'
                           else run.diff_json->'patch'->'patches'
                         end,
                         '[]'::jsonb
                       ) as resume_patch,
                       coalesce(run.diff_json->'changed_paths', '[]'::jsonb) as changed_paths
                from app.jobs_documents document
                join app.jobs_documents source on source.id = document.source_document_id
                  and source.user_id = document.user_id
                left join lateral (
                  select changed_word_ratio, diff_json
                  from app.jobs_tailoring_runs
                  where user_id = document.user_id and output_document_id = document.id
                  order by completed_at desc nulls last, created_at desc limit 1
                ) run on true
                where document.id = %s and document.user_id = %s
                  and document.document_type = 'tailored_resume'
                """,
                (document_id, user_id),
            )
            row = await cursor.fetchone()
        return JobDocumentReview.model_validate(row) if row else None

    async def get_approved_resume_storage_path(
        self, user_id: UUID, application_id: UUID
    ) -> tuple[str, str] | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select storage_path, title
                from app.jobs_documents
                where user_id = %s and application_id = %s
                  and document_type = 'tailored_resume'
                  and status = 'approved' and is_current and storage_path is not null
                order by version desc limit 1
                """,
                (user_id, application_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return str(row["storage_path"]), str(row["title"])

    async def list_answers(self, user_id: UUID) -> list[AnswerLibraryItem]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, normalized_question as question, normalized_key,
                       answer_text, reuse_policy, approved, updated_at
                from app.jobs_answer_library
                where user_id = %s and answer_type = 'long_form'
                order by updated_at desc
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [AnswerLibraryItem.model_validate(row) for row in rows]

    async def create_answer(self, user_id: UUID, item: AnswerLibraryCreate) -> AnswerLibraryItem:
        normalized_key = normalize_answer_key(item.question)
        key = answer_storage_key(normalized_key)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                insert into app.jobs_answer_library (
                  user_id, key, normalized_question, normalized_key, answer_text,
                  answer_type, reuse_policy, approved
                ) values (%s, %s, %s, %s, %s, 'long_form', %s, %s)
                on conflict (user_id, normalized_key) where answer_type = 'long_form'
                do update set normalized_question = excluded.normalized_question,
                              answer_text = excluded.answer_text,
                              reuse_policy = excluded.reuse_policy,
                              approved = excluded.approved
                returning id, normalized_question as question, normalized_key,
                          answer_text, reuse_policy, approved, updated_at
                """,
                (
                    user_id,
                    key,
                    item.question,
                    normalized_key,
                    item.answer_text,
                    item.reuse_policy,
                    item.approved,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Answer insert did not return a row")
        return AnswerLibraryItem.model_validate(row)

    async def update_answer(
        self, user_id: UUID, answer_id: UUID, item: AnswerLibraryUpdate
    ) -> AnswerLibraryItem | None:
        values = item.model_dump(exclude_unset=True)
        if "question" in values:
            question = values.pop("question")
            values["normalized_question"] = question
            values["normalized_key"] = normalize_answer_key(question)
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(key)) for key in values
        )
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                sql.SQL(
                    """
                    update app.jobs_answer_library set {}
                    where id = %s and user_id = %s and answer_type = 'long_form'
                    returning id, normalized_question as question, normalized_key,
                              answer_text, reuse_policy, approved, updated_at
                    """
                ).format(assignments),
                (*values.values(), answer_id, user_id),
            )
            row = await cursor.fetchone()
        return AnswerLibraryItem.model_validate(row) if row else None

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
                  insert into app.jobs_applications (user_id, posting_id)
                  select %s, id from target
                  on conflict (user_id, posting_id) do update set updated_at = now()
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
            profile_cursor = await connection.execute(
                "select to_jsonb(profile) - 'user_id' as profile "
                "from app.jobs_profile profile where user_id = %s",
                (user_id,),
            )
            profile_row = await profile_cursor.fetchone()
            answers_cursor = await connection.execute(
                """
                select id, normalized_question, normalized_key, answer_text,
                       reuse_policy, approved, updated_at
                from app.jobs_answer_library
                where user_id = %s and approved and answer_type = 'long_form'
                order by updated_at desc
                """,
                (user_id,),
            )
            scoped_cursor = await connection.execute(
                """
                select question.question_text, question.normalized_key,
                       question.answer_text, question.answer_source,
                       company.name as company_name, posting.title,
                       question.updated_at
                from app.jobs_application_questions question
                join app.jobs_applications application on application.id = question.application_id
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                where question.user_id = %s and question.status in ('approved', 'submitted')
                  and question.answer_text is not null
                  and (posting.company_id = %s or posting.title = %s)
                order by question.updated_at desc limit 100
                """,
                (user_id, row["company_id"], row["title"]),
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
                (user_id, row["company_id"], row["application_id"]),
            )
            answers = await answers_cursor.fetchall()
            scoped = await scoped_cursor.fetchall()
            duplicates = await duplicate_cursor.fetchall()
        if profile_row is None:
            return None
        return AutofillRunContext(
            **{key: value for key, value in row.items() if key != "company_id"},
            profile=profile_row["profile"],
            approved_answers=[dict(answer) for answer in answers],
            scoped_answers=[dict(answer) for answer in scoped],
            duplicate_company_applications=[dict(item) for item in duplicates],
        )

    async def persist_autofill_step(
        self, user_id: UUID, run_id: UUID, item: AutofillStepCreate
    ) -> list[AutofillFailureIssue] | None:
        issues: list[AutofillFailureIssue] = []
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            run_cursor = await connection.execute(
                """
                update app.jobs_autofill_runs
                set current_step = greatest(current_step, %s), final_url = %s,
                    filled_count = filled_count + %s,
                    skipped_count = skipped_count + %s,
                    failure_count = failure_count + %s
                where id = %s and user_id = %s and status = 'running'
                returning ats, adapter_version
                """,
                (
                    item.step_number,
                    item.url,
                    item.filled_count,
                    item.skipped_count,
                    len(item.failures),
                    run_id,
                    user_id,
                ),
            )
            run = await run_cursor.fetchone()
            if run is None:
                return None
            for failure in item.failures:
                signature = str(failure.get("signature") or "").strip()
                category = str(failure.get("category") or "unsupported_control").strip()
                if not signature:
                    continue
                issue_cursor = await connection.execute(
                    """
                    insert into app.jobs_autofill_failure_issues (
                      user_id, signature, ats, category, field_label, control_kind
                    ) values (%s, %s, %s, %s, %s, %s)
                    on conflict (user_id, signature) do update set
                      occurrence_count = app.jobs_autofill_failure_issues.occurrence_count + 1,
                      last_seen_at = now(),
                      status = case
                        when app.jobs_autofill_failure_issues.status in
                          ('replay_passed', 'live_verified', 'fixed') then 'open'
                        else app.jobs_autofill_failure_issues.status
                      end
                    returning id, signature, ats, category, field_label, control_kind,
                              status, diagnosis, proposed_patch, fixed_commit,
                              verification_notes, occurrence_count, first_seen_at,
                              last_seen_at, updated_at
                    """,
                    (
                        user_id,
                        signature,
                        run["ats"],
                        category,
                        failure.get("field_label"),
                        failure.get("control_kind"),
                    ),
                )
                issue = await issue_cursor.fetchone()
                if issue is None:
                    continue
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
                        issue["id"],
                        run_id,
                        item.step_number,
                        failure.get("field_fingerprint"),
                        failure.get("intended_value"),
                        failure.get("observed_value"),
                        failure.get("error_message"),
                        Jsonb(failure.get("dom_signals") or {}),
                        Jsonb(failure.get("artifact_paths") or {}),
                        run["adapter_version"],
                    ),
                )
                issues.append(AutofillFailureIssue.model_validate(issue))
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
            for answer in item.answers:
                question = str(answer.get("question") or "").strip()
                answer_text = str(answer.get("answer_text") or "").strip()
                if not question or not answer_text:
                    continue
                normalized_key = normalize_answer_key(question)
                source = str(answer.get("source") or "manual")
                reuse_policy = str(answer.get("reuse_policy") or "contextual")
                library_cursor = await connection.execute(
                    """
                    insert into app.jobs_answer_library (
                      user_id, key, normalized_question, normalized_key, answer_text,
                      answer_type, reuse_policy, approved
                    ) values (%s, %s, %s, %s, %s, 'long_form', %s, true)
                    on conflict (user_id, normalized_key) where answer_type = 'long_form'
                    do update set normalized_question = excluded.normalized_question,
                                  answer_text = excluded.answer_text,
                                  reuse_policy = excluded.reuse_policy,
                                  approved = true
                    returning id
                    """,
                    (
                        user_id,
                        answer_storage_key(normalized_key),
                        question,
                        normalized_key,
                        answer_text,
                        reuse_policy
                        if reuse_policy in {"exact", "contextual", "pattern"}
                        else "contextual",
                    ),
                )
                library = await library_cursor.fetchone()
                await connection.execute(
                    """
                    insert into app.jobs_application_questions (
                      user_id, application_id, question_text, normalized_key,
                      answer_text, answer_source, answer_library_id, status
                    ) values (%s, %s, %s, %s, %s, %s, %s, 'approved')
                    """,
                    (
                        user_id,
                        run["application_id"],
                        question,
                        normalized_key,
                        answer_text,
                        source
                        if source in {"jobs_profile", "answer_library", "model", "manual"}
                        else "manual",
                        library["id"] if library else None,
                    ),
                )
            await connection.execute(
                """
                update app.jobs_applications
                set status = 'ready', next_action = 'Review and submit the application manually'
                where id = %s and user_id = %s
                """,
                (run["application_id"], user_id),
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
                update app.jobs_autofill_runs
                set status = %s, completed_at = now()
                where id = %s and user_id = %s and status = 'running'
                returning id
                """,
                (item.status, run_id, user_id),
            )
            return await cursor.fetchone() is not None

    async def list_autofill_failures(self, user_id: UUID) -> list[AutofillFailureIssue]:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, signature, ats, category, field_label, control_kind,
                       status, diagnosis, proposed_patch, fixed_commit,
                       verification_notes, occurrence_count, first_seen_at,
                       last_seen_at, updated_at
                from app.jobs_autofill_failure_issues where user_id = %s
                order by last_seen_at desc
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [AutofillFailureIssue.model_validate(row) for row in rows]

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
                       last_seen_at, updated_at
                from app.jobs_autofill_failure_issues where user_id = %s and id = %s
                """,
                (user_id, issue_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            occurrence_cursor = await connection.execute(
                """
                select id, run_id, step_number, field_fingerprint, intended_value,
                       observed_value, error_message, dom_signals, artifact_paths,
                       adapter_version, created_at
                from app.jobs_autofill_failure_occurrences
                where user_id = %s and issue_id = %s
                order by created_at desc limit 20
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
                select status from app.jobs_autofill_failure_issues
                where user_id = %s and id = %s for update
                """,
                (user_id, issue_id),
            )
            current = await current_cursor.fetchone()
            if current is None:
                return None
            next_status = values.get("status")
            allowed = {
                "open": {"open", "diagnosed"},
                "diagnosed": {"diagnosed", "patch_ready"},
                "patch_ready": {"patch_ready", "replay_passed"},
                "replay_passed": {"replay_passed", "live_verified", "fixed"},
                "live_verified": {"live_verified", "fixed"},
                "fixed": {"fixed"},
            }
            if next_status is not None and next_status not in allowed[str(current["status"])]:
                raise ValueError(
                    f"Invalid failure transition: {current['status']} -> {next_status}"
                )
            if next_status == "fixed" and not values.get("fixed_commit"):
                raise ValueError("A fixed issue requires the verified Git commit")
            if next_status == "replay_passed":
                values["replay_verified_at"] = datetime.now(UTC)
            if next_status in {"live_verified", "fixed"}:
                values["live_verified_at"] = datetime.now(UTC)
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
                              last_seen_at, updated_at
                    """
                ).format(assignments),
                (*values.values(), user_id, issue_id),
            )
            row = await cursor.fetchone()
        return AutofillFailureIssue.model_validate(row) if row else None

    async def claim_task(self, user_id: UUID, worker_id: str) -> WorkerTask | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                with candidate as (
                  select id from app.worker_tasks
                  where user_id = %s and domain = 'jobs'
                    and attempts < max_attempts
                    and available_at <= now()
                    and (
                      status = 'queued'
                      or (status = 'running' and lease_expires_at < now())
                    )
                  order by available_at, created_at
                  for update skip locked
                  limit 1
                )
                update app.worker_tasks task
                set status = 'running', claimed_by = %s, claimed_at = now(),
                    heartbeat_at = now(), lease_expires_at = now() + interval '5 minutes',
                    attempts = attempts + 1, error_message = null
                from candidate
                where task.id = candidate.id
                returning task.id, task.task_type, task.payload, task.attempts,
                          task.max_attempts, task.lease_expires_at
                """,
                (user_id, worker_id),
            )
            row = await cursor.fetchone()
        return WorkerTask.model_validate(row) if row else None

    async def get_worker_application_bundle(
        self, user_id: UUID, application_id: UUID
    ) -> WorkerApplicationBundle | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            application_cursor = await connection.execute(
                """
                select application.id as application_id, posting.application_url,
                       company.name as company_name, posting.title, posting.description_text
                from app.jobs_applications application
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                where application.id = %s and application.user_id = %s
                  and application.status <> 'archived'
                  and posting.application_url is not null
                """,
                (application_id, user_id),
            )
            application = await application_cursor.fetchone()
            if application is None:
                return None
            profile_cursor = await connection.execute(
                """
                select to_jsonb(profile) - 'user_id' as profile
                from app.jobs_profile profile where user_id = %s
                """,
                (user_id,),
            )
            profile_row = await profile_cursor.fetchone()
            resume_cursor = await connection.execute(
                """
                select content_text from app.jobs_documents
                where user_id = %s and document_type = 'master_resume'
                  and status = 'approved' and is_current
                order by version desc limit 1
                """,
                (user_id,),
            )
            resume_row = await resume_cursor.fetchone()
            answers_cursor = await connection.execute(
                """
                select normalized_question, answer_text
                from app.jobs_answer_library
                where user_id = %s and approved and answer_type = 'long_form'
                order by updated_at desc
                """,
                (user_id,),
            )
            answers = await answers_cursor.fetchall()
        if profile_row is None or resume_row is None or not resume_row["content_text"]:
            return None
        try:
            main_resume = json.loads(resume_row["content_text"])
        except (TypeError, json.JSONDecodeError):
            return None
        return WorkerApplicationBundle(
            **application,
            profile=profile_row["profile"],
            main_resume=main_resume,
            approved_answers=[dict(answer) for answer in answers],
        )

    async def get_worker_task_application_id(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> UUID | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select payload->>'application_id' as application_id
                from app.worker_tasks
                where id = %s and user_id = %s and domain = 'jobs' and status = 'running'
                  and claimed_by = %s and lease_expires_at > now()
                """,
                (task_id, user_id, worker_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return UUID(str(row["application_id"]))
        except (TypeError, ValueError):
            return None

    async def get_worker_task_document_id(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> UUID | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select payload->>'document_id' as document_id
                from app.worker_tasks
                where id = %s and user_id = %s and domain = 'jobs' and status = 'running'
                  and claimed_by = %s and lease_expires_at > now()
                """,
                (task_id, user_id, worker_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return UUID(str(row["document_id"]))
        except (TypeError, ValueError):
            return None

    async def heartbeat_task(self, user_id: UUID, task_id: UUID, worker_id: str) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.worker_tasks
                set heartbeat_at = now(), lease_expires_at = now() + interval '5 minutes'
                where id = %s and user_id = %s and status = 'running'
                  and claimed_by = %s and lease_expires_at > now()
                """,
                (task_id, user_id, worker_id),
            )
        return int(cursor.rowcount) == 1

    async def complete_task(
        self, user_id: UUID, task_id: UUID, worker_id: str, result: dict[str, Any]
    ) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            task_cursor = await connection.execute(
                """
                select task_type, payload from app.worker_tasks
                where id = %s and user_id = %s and status = 'running' and claimed_by = %s
                  and lease_expires_at > now()
                for update
                """,
                (task_id, user_id, worker_id),
            )
            task_row = await task_cursor.fetchone()
            if task_row is None:
                return False

            # Preview tasks carry a document_id instead of an application_id.
            if task_row["task_type"] == "preview_resume":
                storage_path = result.get("storage_path")
                document_id_text = task_row["payload"].get("document_id")
                if not isinstance(storage_path, str) or not storage_path.strip():
                    return False
                try:
                    document_id = UUID(str(document_id_text))
                except (TypeError, ValueError):
                    return False
                await connection.execute(
                    """
                    update app.jobs_documents
                    set storage_path = %s, updated_at = now()
                    where id = %s and user_id = %s
                      and document_type = 'master_resume' and status = 'draft'
                    """,
                    (storage_path, document_id, user_id),
                )
                preview_cursor = await connection.execute(
                    """
                    update app.worker_tasks
                    set status = 'completed', result = %s, completed_at = now(),
                        lease_expires_at = null, heartbeat_at = now()
                    where id = %s and user_id = %s and status = 'running'
                      and claimed_by = %s and lease_expires_at > now()
                    """,
                    (Jsonb(result), task_id, user_id, worker_id),
                )
                return int(preview_cursor.rowcount) == 1

            application_id_text = task_row["payload"].get("application_id")
            try:
                application_id = UUID(str(application_id_text))
            except (TypeError, ValueError):
                return False

            if task_row["task_type"] == "autofill_application":
                missing = result.get("unanswered_required_questions", [])
                if not isinstance(missing, list) or not all(
                    isinstance(item, str) for item in missing
                ):
                    return False
                labels = [item.strip() for item in missing if item.strip()]
                await connection.execute(
                    """
                    update app.jobs_application_questions
                    set status = 'approved'
                    where user_id = %s and application_id = %s
                    """,
                    (user_id, application_id),
                )
                for position, label in enumerate(dict.fromkeys(labels)):
                    updated_cursor = await connection.execute(
                        """
                        update app.jobs_application_questions
                        set status = 'unanswered', display_order = %s
                        where user_id = %s and application_id = %s and question_text = %s
                        """,
                        (position, user_id, application_id, label),
                    )
                    if int(updated_cursor.rowcount) == 0:
                        await connection.execute(
                            """
                            insert into app.jobs_application_questions (
                              user_id, application_id, question_text, status, display_order
                            ) values (%s, %s, %s, 'unanswered', %s)
                            """,
                            (user_id, application_id, label, position),
                        )
                await connection.execute(
                    """
                    update app.jobs_applications
                    set status = 'ready',
                        next_action = 'Review the autofilled application and submit manually',
                        next_action_at = null
                    where id = %s and user_id = %s and status <> 'archived'
                    """,
                    (application_id, user_id),
                )

            description = result.get("posting_description")
            if isinstance(description, str) and description.strip():
                await connection.execute(
                    """
                    update app.jobs_postings posting
                    set description_text = %s
                    from app.jobs_applications application
                    where application.id = %s and application.user_id = %s
                      and posting.id = application.posting_id
                    """,
                    (description[:100000], application_id, user_id),
                )

            form_fields = result.get("form_fields")
            if isinstance(form_fields, list):
                for position, field in enumerate(form_fields):
                    if not isinstance(field, dict) or not field.get("required"):
                        continue
                    label = str(field.get("label", "")).strip()
                    if not label:
                        continue
                    await connection.execute(
                        """
                        insert into app.jobs_application_questions (
                          user_id, application_id, question_text, status, display_order
                        )
                        select %s, %s, %s, 'unanswered', %s
                        where not exists (
                          select 1 from app.jobs_application_questions
                          where user_id = %s and application_id = %s and question_text = %s
                        )
                        """,
                        (
                            user_id,
                            application_id,
                            label,
                            position,
                            user_id,
                            application_id,
                            label,
                        ),
                    )

            tailored_resume = result.get("tailored_resume")
            if isinstance(tailored_resume, dict):
                storage_path = result.get("storage_path")
                if not isinstance(storage_path, str) or not storage_path.strip():
                    return False
                source_cursor = await connection.execute(
                    """
                    select id from app.jobs_documents
                    where user_id = %s and document_type = 'master_resume'
                      and status = 'approved' and is_current
                    order by version desc limit 1
                    """,
                    (user_id,),
                )
                source_document = await source_cursor.fetchone()
                family_cursor = await connection.execute(
                    """
                    select family_id, coalesce(max(version), 0) as version
                    from app.jobs_documents
                    where user_id = %s and application_id = %s
                      and document_type = 'tailored_resume'
                    group by family_id order by max(version) desc limit 1
                    """,
                    (user_id, application_id),
                )
                family = await family_cursor.fetchone()
                family_id = family["family_id"] if family else uuid4()
                version = int(family["version"] if family else 0) + 1
                await connection.execute(
                    """
                    update app.jobs_documents set is_current = false
                    where user_id = %s and family_id = %s and is_current
                    """,
                    (user_id, family_id),
                )
                company_cursor = await connection.execute(
                    """
                    select company.name from app.jobs_applications application
                    join app.jobs_postings posting on posting.id = application.posting_id
                    join app.jobs_companies company on company.id = posting.company_id
                    where application.id = %s and application.user_id = %s
                    """,
                    (application_id, user_id),
                )
                company = await company_cursor.fetchone()
                document_id = uuid4()
                await connection.execute(
                    """
                    insert into app.jobs_documents (
                      id, user_id, application_id, document_type, title,
                      family_id, version, source_document_id, content_text, storage_path,
                      mime_type, status, is_current
                    ) values (
                      %s, %s, %s, 'tailored_resume', %s,
                      %s, %s, %s, %s, %s,
                      'application/json', 'draft', true
                    )
                    """,
                    (
                        document_id,
                        user_id,
                        application_id,
                        f"Tailored resume — {company['name'] if company else 'Application'}",
                        family_id,
                        version,
                        source_document["id"] if source_document else None,
                        json.dumps(tailored_resume, ensure_ascii=False),
                        storage_path,
                    ),
                )
                changed_ratio = result.get("changed_word_ratio")
                await connection.execute(
                    """
                    insert into app.jobs_tailoring_runs (
                      user_id, application_id, worker_task_id, run_type, status,
                      provider, model, prompt_version, input_document_id,
                      output_document_id, changed_word_ratio, diff_json, validation_json,
                      started_at, completed_at
                    ) values (
                      %s, %s, %s, 'resume', 'completed',
                      'claude-code', 'subscription', 'minimal-patch-v1', %s,
                      %s, %s, %s, %s,
                      now(), now()
                    )
                    """,
                    (
                        user_id,
                        application_id,
                        task_id,
                        source_document["id"] if source_document else None,
                        document_id,
                        changed_ratio if isinstance(changed_ratio, int | float) else None,
                        Jsonb(
                            {
                                "patch": result.get("resume_patch"),
                                "changed_paths": result.get("changed_paths", []),
                            }
                        ),
                        Jsonb({"approval_required": True, "submitted": False}),
                    ),
                )
            cursor = await connection.execute(
                """
                update app.worker_tasks
                set status = 'completed', result = %s, completed_at = now(),
                    lease_expires_at = null, heartbeat_at = now()
                where id = %s and user_id = %s and status = 'running' and claimed_by = %s
                  and lease_expires_at > now()
                """,
                (Jsonb(result), task_id, user_id, worker_id),
            )
        return int(cursor.rowcount) == 1

    async def fail_task(
        self,
        user_id: UUID,
        task_id: UUID,
        worker_id: str,
        error_message: str,
        *,
        retry: bool,
    ) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.worker_tasks
                set status = case
                      when %s and attempts < max_attempts then 'queued' else 'failed'
                    end,
                    available_at = case
                      when %s then now() + interval '30 seconds' else available_at
                    end,
                    error_message = %s, lease_expires_at = null,
                    completed_at = case when %s and attempts < max_attempts then null else now() end
                where id = %s and user_id = %s and status = 'running' and claimed_by = %s
                  and lease_expires_at > now()
                """,
                (retry, retry, error_message, retry, task_id, user_id, worker_id),
            )
        return int(cursor.rowcount) == 1


async def get_jobs_store(request: Request) -> JobsStore:
    settings: Settings = request.app.state.settings
    return PostgresJobsStore(request.app.state.pool, settings.database_pool_timeout_seconds)
