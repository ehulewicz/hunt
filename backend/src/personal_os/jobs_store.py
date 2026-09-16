from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from personal_os.config import Settings
from personal_os.jobs_answers import answer_storage_key, normalize_answer_key
from personal_os.jobs_models import (
    DEFAULT_TECHNICAL_FAMILIES,
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
    JobApplication,
    JobDocument,
    JobListing,
    JobListingPage,
    JobsProfile,
    JobsProfileUpdate,
    ListingSyncResponse,
    MasterResumeContent,
)
from personal_os.jobs_sources import SOURCE_NAME, SourceListing

ANSWER_SOURCES = frozenset({"jobs_profile", "answer_library", "model", "pattern", "manual"})

# Every non-data-science open listing without an active application,
# numbered once: default technical families first, newest first, ties by id.
BOARD_QUERY = sql.SQL(
    """
    select posting.id, company.name as company_name, posting.title,
           posting.location, posting.salary_text, posting.application_url,
           posting.discovery_url, posting.role_families,
           posting.source_category, posting.normalized_locations,
           posting.career_stage, posting.feed_region,
           posting.source_posted_at, posting.source_age_days,
           posting.status, application.id as application_id,
           application.status as application_status,
           row_number() over (
             order by case when posting.role_families && %s::text[] then 0 else 1 end,
                      posting.source_age_days asc nulls last,
                      posting.source_posted_at desc nulls last,
                      posting.created_at desc,
                      posting.id
           ) as "row"
    from app.jobs_postings posting
    join app.jobs_companies company on company.id = posting.company_id
    left join app.jobs_applications application
      on application.user_id = posting.user_id and application.posting_id = posting.id
     and application.status <> 'archived'
    where posting.user_id = %s
      and posting.status not in ('closed', 'archived')
      and posting.career_stage = 'internship'
      and posting.feed_region = 'usa'
      and not (posting.role_families && array['data_science']::text[])
      and application.id is null
    """
)


class AmbiguousReference(LookupError):
    """An id prefix matched more than one listing."""


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

    async def get_listing(self, user_id: UUID, reference: str) -> JobListing | None: ...

    async def create_application(
        self, user_id: UUID, posting_id: UUID, status: str
    ) -> JobApplication | None: ...

    async def list_applications(self, user_id: UUID) -> list[JobApplication]: ...

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None: ...

    async def archive_application(self, user_id: UUID, application_id: UUID) -> bool: ...

    async def list_documents(self, user_id: UUID) -> list[JobDocument]: ...

    async def create_master_resume_draft(
        self, user_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None: ...

    async def update_master_resume_draft(
        self, user_id: UUID, document_id: UUID, content: MasterResumeContent
    ) -> JobDocument | None: ...

    async def approve_document(self, user_id: UUID, document_id: UUID) -> JobDocument | None: ...

    async def get_document_storage_path(
        self, user_id: UUID, document_id: UUID
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
    "graduation_term",
    "completed_coop_terms",
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
    "minimum_salary_period",
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
        """Board listings not yet applied to, each carrying its canonical row number.

        Data-science listings remain stored but leave the board before numbering.
        The number is computed over the remaining board before any filter applies,
        so a listing keeps the same number in every view; filtered views show
        gaps. Default technical families come first so the everyday view is
        contiguous and `--all-roles` extends it rather than reshuffling it.
        """
        conditions: list[sql.Composable] = []
        params: list[Any] = [list(DEFAULT_TECHNICAL_FAMILIES), user_id]
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
        where = (
            sql.SQL("where ") + sql.SQL(" and ").join(conditions) if conditions else sql.SQL("")
        )

        def select(columns: str, tail: str = "") -> sql.Composed:
            return sql.SQL("with board as ({}) select {} from board posting {} {}").format(
                BOARD_QUERY, sql.SQL(columns), where, sql.SQL(tail)
            )

        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            location_cursor = await connection.execute(
                sql.SQL(
                    """
                    with board as ({})
                    select distinct normalized.location as location
                    from board posting
                    cross join lateral unnest(posting.normalized_locations)
                      as normalized(location)
                    {}
                    order by normalized.location
                    """
                ).format(
                    BOARD_QUERY,
                    sql.SQL("where ") + sql.SQL(" and ").join(facet_conditions)
                    if facet_conditions
                    else sql.SQL(""),
                ),
                facet_params,
            )
            location_rows = await location_cursor.fetchall()
            count_cursor = await connection.execute(select("count(*) as total"), params)
            count = await count_cursor.fetchone()
            rows_cursor = await connection.execute(
                select("posting.*", 'order by posting."row" limit %s offset %s'),
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

    async def get_listing(self, user_id: UUID, reference: str) -> JobListing | None:
        """A listing by its canonical row number, or by a prefix of its id.

        A row number resolves against the same board the listing views show. An
        id prefix resolves against every listing, applied-to ones included, so
        a hidden listing can still be opened; several matches are ambiguous.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            if reference.isdigit():
                cursor = await connection.execute(
                    sql.SQL("with board as ({}) select posting.* from board posting "
                            'where posting."row" = %s').format(BOARD_QUERY),
                    (list(DEFAULT_TECHNICAL_FAMILIES), user_id, int(reference)),
                )
                row = await cursor.fetchone()
                return JobListing.model_validate(row) if row else None
            prefix = reference.replace("-", "").casefold()
            if len(prefix) < 4 or any(char not in "0123456789abcdef" for char in prefix):
                return None
            cursor = await connection.execute(
                """
                select posting.id, company.name as company_name, posting.title,
                       posting.location, posting.salary_text, posting.application_url,
                       posting.discovery_url, posting.role_families,
                       posting.source_category, posting.normalized_locations,
                       posting.career_stage, posting.feed_region,
                       posting.source_posted_at, posting.source_age_days,
                       posting.status, application.id as application_id,
                       application.status as application_status
                from app.jobs_postings posting
                join app.jobs_companies company on company.id = posting.company_id
                left join app.jobs_applications application
                  on application.user_id = posting.user_id
                 and application.posting_id = posting.id
                 and application.status <> 'archived'
                where posting.user_id = %s
                  and replace(posting.id::text, '-', '') like %s
                limit 2
                """,
                (user_id, f"{prefix}%"),
            )
            rows = await cursor.fetchall()
            if len(rows) > 1:
                raise AmbiguousReference(reference)
            return JobListing.model_validate(rows[0]) if rows else None

    async def create_application(
        self, user_id: UUID, posting_id: UUID, status: str
    ) -> JobApplication | None:
        """Create one lifecycle row without introducing a backlog or batch queue."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                insert into app.jobs_applications (user_id, posting_id, status)
                select %s, id, %s from app.jobs_postings
                where id = %s and user_id = %s and application_url is not null
                on conflict (user_id, posting_id) do update set
                  updated_at = now(),
                  status = case when app.jobs_applications.status = 'archived'
                                then excluded.status else app.jobs_applications.status end
                returning id
                """,
                (user_id, status, posting_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        applications = await self.list_applications(user_id)
        application_id = UUID(str(row["id"]))
        return next((item for item in applications if item.id == application_id), None)

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
                       coalesce(questions.items, array[]::text[])
                         as unanswered_required_questions
                from app.jobs_applications application
                join app.jobs_postings posting on posting.id = application.posting_id
                join app.jobs_companies company on company.id = posting.company_id
                left join lateral (
                  select array_agg(question_text order by display_order, created_at) as items
                  from app.jobs_application_questions
                  where user_id = application.user_id
                    and application_id = application.id and status = 'unanswered'
                ) questions on true
                where application.user_id = %s and application.status <> 'archived'
                order by case application.status
                           when 'applying' then 0 when 'applied' then 1 when 'oa' then 2
                           when 'oa_completed' then 3 when 'interview' then 4
                           when 'offer' then 5 else 6 end,
                         application.updated_at desc, application.id
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [JobApplication.model_validate(row) for row in rows]

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None:
        values = update.model_dump(exclude_unset=True)
        if values.get("status") == "applied":
            values["applied_at"] = datetime.now(UTC)
        elif values.get("status") == "applying":
            values["applied_at"] = None
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
                select id, normalized_question as question, normalized_key,
                       answer_text, reuse_policy, approved, updated_at
                from app.jobs_answer_library
                where user_id = %s and answer_type = 'long_form'
                order by updated_at desc, id
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
                join app.jobs_answer_library library
                  on library.id = question.answer_library_id
                 and library.user_id = question.user_id
                 and library.approved
                 and library.answer_text = question.answer_text
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
                returning application_id, ats, adapter_version
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
            for observation in item.observations:
                source = observation.source if observation.source in ANSWER_SOURCES else "manual"
                # A retried step re-sends the same events; the (run_id, event_index)
                # key makes the replay a no-op instead of duplicate history.
                await connection.execute(
                    """
                    insert into app.jobs_application_questions (
                      user_id, application_id, question_text, normalized_key,
                      answer_text, answer_source, answer_library_id, status,
                      run_id, step_number, page_url, control_kind, field_fingerprint,
                      resolver_id, proposed_value, event_index, event_kind
                    ) values (
                      %s, %s, %s, %s, %s, %s, null, 'drafted',
                      %s, %s, %s, %s, %s, %s, %s, %s, 'observed'
                    )
                    on conflict (run_id, event_index) do nothing
                    """,
                    (
                        user_id,
                        run["application_id"],
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
                    ),
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
            for answer in item.answers:
                question = str(answer.get("question") or "").strip()
                answer_text = str(answer.get("answer_text") or "").strip()
                if not question or not answer_text:
                    continue
                normalized_key = normalize_answer_key(question)
                source = str(answer.get("source") or "manual")
                await connection.execute(
                    """
                    insert into app.jobs_application_questions (
                      user_id, application_id, question_text, normalized_key,
                      answer_text, answer_source, answer_library_id, status,
                      run_id, step_number, page_url, control_kind, field_fingerprint,
                      resolver_id, proposed_value, event_kind
                    ) values (
                      %s, %s, %s, %s, %s, %s, null, %s,
                      %s, %s, %s, %s, %s, %s, %s, 'final'
                    )
                    """,
                    (
                        user_id,
                        run["application_id"],
                        question,
                        normalized_key,
                        answer_text,
                        source if source in ANSWER_SOURCES else "manual",
                        "submitted" if item.submitted else "drafted",
                        run_id,
                        answer.get("step_number"),
                        answer.get("page_url"),
                        answer.get("control_kind"),
                        answer.get("field_fingerprint"),
                        answer.get("resolver_id"),
                        answer.get("proposed_value"),
                    ),
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
                order by last_seen_at desc, id
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

async def get_jobs_store(request: Request) -> JobsStore:
    settings: Settings = request.app.state.settings
    return PostgresJobsStore(request.app.state.pool, settings.database_pool_timeout_seconds)
