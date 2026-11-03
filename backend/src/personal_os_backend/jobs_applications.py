"""Applications: one lifecycle row per posting you apply to, listed in lifecycle order."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from psycopg import sql

from personal_os_backend.config import StoreBase
from personal_os_backend.jobs_models import ApplicationUpdate, JobApplication


class ApplicationsStore(Protocol):
    async def create_application(
        self, user_id: UUID, posting_id: UUID, status: str
    ) -> JobApplication | None: ...

    async def list_applications(self, user_id: UUID) -> list[JobApplication]: ...

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None: ...

    async def archive_application(self, user_id: UUID, application_id: UUID) -> bool: ...


class ApplicationsMixin(StoreBase):
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
