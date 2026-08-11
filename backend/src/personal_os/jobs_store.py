from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from fastapi import Request
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from personal_os.config import Settings
from personal_os.jobs_models import (
    AnswerLibraryItem,
    AnswerLibraryUpsert,
    ApplicationUpdate,
    BatchPrepareResponse,
    JobApplication,
    JobDocument,
    JobListing,
    JobListingPage,
    JobsProfile,
    JobsProfileUpdate,
    ListingSyncResponse,
    WorkerApplicationBundle,
    WorkerCredential,
    WorkerTask,
)
from personal_os.jobs_sources import SOURCE_NAME, SourceListing


class JobsStore(Protocol):
    async def get_profile(self, user_id: UUID) -> JobsProfile: ...

    async def update_profile(
        self, user_id: UUID, update: JobsProfileUpdate
    ) -> JobsProfile: ...

    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse: ...

    async def list_listings(
        self,
        user_id: UUID,
        *,
        career_stage: str | None,
        role_family: str | None,
        fresh_only: bool,
        query: str | None,
        limit: int,
        offset: int,
    ) -> JobListingPage: ...

    async def prepare_applications(
        self, user_id: UUID, posting_ids: Sequence[UUID]
    ) -> BatchPrepareResponse: ...

    async def list_applications(self, user_id: UUID) -> list[JobApplication]: ...

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None: ...

    async def list_documents(self, user_id: UUID) -> list[JobDocument]: ...

    async def approve_document(self, user_id: UUID, document_id: UUID) -> JobDocument | None: ...

    async def get_document_storage_path(
        self, user_id: UUID, document_id: UUID
    ) -> tuple[str, str] | None: ...

    async def list_answers(self, user_id: UUID) -> list[AnswerLibraryItem]: ...

    async def upsert_answer(
        self, user_id: UUID, item: AnswerLibraryUpsert
    ) -> AnswerLibraryItem: ...

    async def create_worker_credential(
        self,
        user_id: UUID,
        *,
        credential_id: UUID,
        name: str,
        token_hash: str,
        expires_at: datetime,
    ) -> WorkerCredential: ...

    async def list_worker_credentials(self, user_id: UUID) -> list[WorkerCredential]: ...

    async def revoke_worker_credential(self, user_id: UUID, credential_id: UUID) -> bool: ...

    async def validate_worker_credential(
        self, user_id: UUID, credential_id: UUID, token: str
    ) -> bool: ...

    async def claim_task(self, user_id: UUID, worker_id: str) -> WorkerTask | None: ...

    async def get_worker_application_bundle(
        self, user_id: UUID, application_id: UUID
    ) -> WorkerApplicationBundle | None: ...

    async def get_worker_task_application_id(
        self, user_id: UUID, task_id: UUID
    ) -> UUID | None: ...

    async def heartbeat_task(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> bool: ...

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

    async def update_profile(
        self, user_id: UUID, update: JobsProfileUpdate
    ) -> JobsProfile:
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
            for listing in listings:
                company_cursor = await connection.execute(
                    """
                    insert into app.jobs_companies (user_id, name, website_url)
                    values (%s, %s, %s)
                    on conflict (user_id, name) do update
                    set website_url = coalesce(excluded.website_url, app.jobs_companies.website_url)
                    returning id
                    """,
                    (user_id, listing.company, listing.company_url),
                )
                company = await company_cursor.fetchone()
                if company is None:
                    raise RuntimeError("Company upsert did not return an id")
                await connection.execute(
                    """
                    insert into app.jobs_postings (
                      user_id, company_id, title, location, employment_type,
                      source, source_url, external_id, salary_text, status,
                      discovered_at, application_url, discovery_url, role_families,
                      career_stage, feed_region, source_posted_at, source_age_days,
                      last_seen_at
                    )
                    values (
                      %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s, %s, %s,
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
                      application_url = excluded.application_url,
                      discovery_url = excluded.discovery_url,
                      role_families = excluded.role_families,
                      career_stage = excluded.career_stage,
                      feed_region = excluded.feed_region,
                      source_posted_at = excluded.source_posted_at,
                      source_age_days = excluded.source_age_days,
                      last_seen_at = excluded.last_seen_at
                    """,
                    (
                        user_id,
                        company["id"],
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
                        listing.career_stage,
                        listing.feed_region,
                        listing.source_posted_at,
                        listing.source_age_days,
                        sync_started,
                    ),
                )

            await connection.execute(
                """
                update app.jobs_postings posting
                set status = 'closed'
                where posting.user_id = %s
                  and posting.source = %s
                  and posting.last_seen_at < %s
                  and posting.status in ('saved', 'reviewing')
                  and not exists (
                    select 1 from app.jobs_applications application
                    where application.posting_id = posting.id
                  )
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
        career_stage: str | None,
        role_family: str | None,
        fresh_only: bool,
        query: str | None,
        limit: int,
        offset: int,
    ) -> JobListingPage:
        conditions = [sql.SQL("posting.user_id = %s"), sql.SQL("posting.status <> 'archived'")]
        params: list[Any] = [user_id]
        if career_stage:
            conditions.append(sql.SQL("posting.career_stage = %s"))
            params.append(career_stage)
        if role_family:
            conditions.append(sql.SQL("%s = any(posting.role_families)"))
            params.append(role_family)
        if fresh_only:
            conditions.append(sql.SQL("posting.source_age_days between 0 and 1"))
        if query and query.strip():
            conditions.append(
                sql.SQL(
                    "(posting.title ilike %s or company.name ilike %s "
                    "or posting.location ilike %s)"
                )
            )
            like = f"%{query.strip()}%"
            params.extend((like, like, like))
        where = sql.SQL(" and ").join(conditions)
        base = sql.SQL(
            """
            from app.jobs_postings posting
            join app.jobs_companies company on company.id = posting.company_id
            left join app.jobs_applications application
              on application.user_id = posting.user_id and application.posting_id = posting.id
            where {}
            """
        ).format(where)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
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
        )

    async def prepare_applications(
        self, user_id: UUID, posting_ids: Sequence[UUID]
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
                    on conflict (user_id, posting_id) do update set updated_at = now()
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
                      jsonb_build_object('application_id', %s::text)
                    )
                    """,
                    (task_id, user_id, application_id),
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
                       application.created_at, application.updated_at
                from app.jobs_applications application
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                where application.user_id = %s
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

    async def approve_document(self, user_id: UUID, document_id: UUID) -> JobDocument | None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            selected_cursor = await connection.execute(
                """
                select family_id, document_type, storage_path from app.jobs_documents
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
            await connection.execute(
                """
                update app.jobs_applications application
                set status = 'ready'
                from app.jobs_documents document
                where document.id = %s and document.user_id = %s
                  and document.document_type = 'tailored_resume'
                  and application.id = document.application_id
                  and application.status = 'preparing'
                """,
                (document_id, user_id),
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

    async def list_answers(self, user_id: UUID) -> list[AnswerLibraryItem]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, key, normalized_question, answer_text, answer_type,
                       approved, updated_at
                from app.jobs_answer_library
                where user_id = %s
                order by key
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [AnswerLibraryItem.model_validate(row) for row in rows]

    async def upsert_answer(
        self, user_id: UUID, item: AnswerLibraryUpsert
    ) -> AnswerLibraryItem:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                insert into app.jobs_answer_library (
                  user_id, key, normalized_question, answer_text, answer_type, approved
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (user_id, key) do update set
                  normalized_question = excluded.normalized_question,
                  answer_text = excluded.answer_text,
                  answer_type = excluded.answer_type,
                  approved = excluded.approved
                returning id, key, normalized_question, answer_text, answer_type,
                          approved, updated_at
                """,
                (
                    user_id,
                    item.key,
                    item.normalized_question,
                    item.answer_text,
                    item.answer_type,
                    item.approved,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Answer upsert did not return a row")
        return AnswerLibraryItem.model_validate(row)

    async def create_worker_credential(
        self,
        user_id: UUID,
        *,
        credential_id: UUID,
        name: str,
        token_hash: str,
        expires_at: datetime,
    ) -> WorkerCredential:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                insert into app.jobs_worker_credentials (
                  id, user_id, name, token_hash, expires_at
                ) values (%s, %s, %s, %s, %s)
                returning id, name, scopes, expires_at, last_used_at, revoked_at, created_at
                """,
                (credential_id, user_id, name, token_hash, expires_at),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Credential insert did not return a row")
        return WorkerCredential.model_validate(row)

    async def list_worker_credentials(self, user_id: UUID) -> list[WorkerCredential]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id, name, scopes, expires_at, last_used_at, revoked_at, created_at
                from app.jobs_worker_credentials
                where user_id = %s
                order by created_at desc
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [WorkerCredential.model_validate(row) for row in rows]

    async def revoke_worker_credential(self, user_id: UUID, credential_id: UUID) -> bool:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_worker_credentials set revoked_at = now()
                where id = %s and user_id = %s and revoked_at is null
                """,
                (credential_id, user_id),
            )
        return int(cursor.rowcount) == 1

    async def validate_worker_credential(
        self, user_id: UUID, credential_id: UUID, token: str
    ) -> bool:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                update app.jobs_worker_credentials
                set last_used_at = now()
                where id = %s and user_id = %s and token_hash = %s
                  and revoked_at is null and expires_at > now()
                """,
                (credential_id, user_id, token_hash),
            )
        return int(cursor.rowcount) == 1

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
                select key, normalized_question, answer_text, answer_type
                from app.jobs_answer_library
                where user_id = %s and approved
                order by key
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
        self, user_id: UUID, task_id: UUID
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
                """,
                (task_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return UUID(str(row["application_id"]))
        except (TypeError, ValueError):
            return None

    async def heartbeat_task(
        self, user_id: UUID, task_id: UUID, worker_id: str
    ) -> bool:
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
                select payload from app.worker_tasks
                where id = %s and user_id = %s and status = 'running' and claimed_by = %s
                for update
                """,
                (task_id, user_id, worker_id),
            )
            task_row = await task_cursor.fetchone()
            if task_row is None:
                return False
            application_id_text = task_row["payload"].get("application_id")
            try:
                application_id = UUID(str(application_id_text))
            except (TypeError, ValueError):
                return False

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
                        {
                            "patch": result.get("resume_patch"),
                            "changed_paths": result.get("changed_paths", []),
                        },
                        {"approval_required": True, "submitted": False},
                    ),
                )
            cursor = await connection.execute(
                """
                update app.worker_tasks
                set status = 'completed', result = %s, completed_at = now(),
                    lease_expires_at = null, heartbeat_at = now()
                where id = %s and user_id = %s and status = 'running' and claimed_by = %s
                """,
                (result, task_id, user_id, worker_id),
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
                """,
                (retry, retry, error_message, retry, task_id, user_id, worker_id),
            )
        return int(cursor.rowcount) == 1


async def get_jobs_store(request: Request) -> JobsStore:
    settings: Settings = request.app.state.settings
    return PostgresJobsStore(request.app.state.pool, settings.database_pool_timeout_seconds)
