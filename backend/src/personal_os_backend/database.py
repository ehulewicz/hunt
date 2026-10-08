import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from personal_os_backend.config import Settings


def canonical_resume_path() -> Path:
    configured = os.environ.get("PERSONAL_OS_RESUME_JSON")
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "artifacts" / "career" / "main.json"
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Canonical resume not found; expected artifacts/career/main.json "
        "or PERSONAL_OS_RESUME_JSON"
    )


class BootstrapStore(Protocol):
    async def bootstrap(self, user_id: UUID, email: str) -> None: ...


class PostgresBootstrapStore:
    def __init__(self, pool: AsyncConnectionPool, timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def bootstrap(self, user_id: UUID, email: str) -> None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            await connection.execute(
                """
                insert into app.jobs_profile (user_id, email)
                values (%s, %s)
                on conflict (user_id) do nothing
                """,
                (user_id, email),
            )
            await connection.execute(
                """
                update app.jobs_profile set
                  legal_name = coalesce(legal_name, 'Alex Example'),
                  preferred_name = coalesce(preferred_name, 'Ethan'),
                  phone = coalesce(phone, '555-010-0199'),
                  address_line_1 = coalesce(address_line_1, '100 Example Ave'),
                  city = coalesce(city, 'Atlanta'),
                  region = coalesce(region, 'Georgia'),
                  postal_code = coalesce(postal_code, '00000'),
                  country = coalesce(country, 'United States'),
                  linkedin_url = coalesce(
                    linkedin_url, 'https://www.linkedin.com/in/alex-example'
                  ),
                  github_url = coalesce(github_url, 'https://github.com/ehulewicz'),
                  work_authorization_status = coalesce(work_authorization_status, 'US Citizen'),
                  visa_status = coalesce(visa_status, 'Citizen'),
                  sponsorship_required = coalesce(sponsorship_required, false),
                  school_name = coalesce(school_name, 'Example Institute of Technology'),
                  degree_type = coalesce(degree_type, 'B.S.'),
                  field_of_study = coalesce(field_of_study, 'Computer Science'),
                  graduation_date = coalesce(graduation_date, date '2027-12-15'),
                  graduation_term = coalesce(graduation_term, 'Fall'),
                  completed_coop_terms = coalesce(completed_coop_terms, 4.5),
                  gpa = coalesce(gpa, 3.78),
                  earliest_start_text = coalesce(
                    earliest_start_text, 'Two weeks from offer acceptance.'
                  ),
                  years_professional_experience = coalesce(
                    years_professional_experience, 3
                  ),
                  willing_to_relocate = coalesce(willing_to_relocate, true),
                  willing_to_travel = coalesce(willing_to_travel, true),
                  target_roles = case
                    when target_roles is null or cardinality(target_roles) = 0 then
                      array[
                        'Software Engineering', 'Data Engineering',
                        'Machine Learning Engineering', 'Data Science'
                      ]::text[]
                    else target_roles
                  end,
                  target_career_stages = case
                    when cardinality(target_career_stages) = 0 then array['internship']::text[]
                    else target_career_stages
                  end,
                  minimum_salary = coalesce(minimum_salary, 35),
                  minimum_salary_period = coalesce(minimum_salary_period, 'hour')
                where user_id = %s
                """,
                (user_id,),
            )
            resume = canonical_resume_path().read_text(encoding="utf-8")
            await connection.execute(
                """
                insert into app.jobs_documents (
                  user_id, document_type, title, content_text, mime_type, status, is_current
                )
                select %s, 'master_resume', 'Main resume', %s, 'application/json',
                       'approved', true
                where not exists (
                  select 1 from app.jobs_documents
                  where user_id = %s and document_type = 'master_resume' and is_current
                )
                """,
                (user_id, resume, user_id),
            )


def create_pool(settings: Settings) -> AsyncConnectionPool[Any]:
    return AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        timeout=settings.database_pool_timeout_seconds,
        open=False,
        check=AsyncConnectionPool.check_connection,
        max_lifetime=1800,
        kwargs={"row_factory": dict_row},
    )


async def get_bootstrap_store(request: Request) -> BootstrapStore:
    settings: Settings = request.app.state.settings
    return PostgresBootstrapStore(request.app.state.pool, settings.database_pool_timeout_seconds)


async def database_is_ready(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    try:
        async with (
            request.app.state.pool.connection(
                timeout=settings.database_pool_timeout_seconds
            ) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "select to_regclass('app.jobs_profile') is not null"
            )
            row = await cursor.fetchone()
            return bool(row and next(iter(row.values())))
    except Exception:
        return False


@asynccontextmanager
async def opened_pool(pool: AsyncConnectionPool[Any]) -> AsyncIterator[None]:
    await pool.open(wait=True)
    try:
        yield
    finally:
        await pool.close()
