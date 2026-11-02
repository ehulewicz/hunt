"""The Learned section of the Hunt Inbox: what the run taught itself.

Four lists: the code fixes the fixer kept and how each stands (on trial,
proven by a later run, or retired); the branches active patterns take that a
model taught; the unsolved problems waiting for Retry; and each recent run's
model usage by lane.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_changes import CHANGE_COLUMNS


class LearnedFix(BaseModel):
    """A learned code file and how it stands."""

    change_id: UUID
    commit: str
    file: str
    cause: str | None
    question: str | None
    site: str | None
    issue_id: UUID | None
    checks: dict[str, Any] | None
    probe: dict[str, Any] | None
    summary: str | None
    status: str  # trial | proven | retired
    created_at: datetime


class LearnedBranch(BaseModel):
    """An active pattern a model taught: an expression or a binding."""

    pattern_id: UUID | None
    fact_key: str | None
    expression: str | None
    question: str | None
    created_at: datetime


class LearnedUnsolved(BaseModel):
    """A problem the fixer ran out of attempts on."""

    issue_id: UUID
    cause: str
    site: str | None
    label: str | None
    attempts: str | None
    created_at: datetime


class LearnedRun(BaseModel):
    run_id: UUID
    site: str | None
    started_at: datetime
    model_calls: int
    model_usage: dict[str, dict[str, int]]


class LearnedReport(BaseModel):
    fixes: list[LearnedFix] = Field(default_factory=list)
    branches: list[LearnedBranch] = Field(default_factory=list)
    unsolved: list[LearnedUnsolved] = Field(default_factory=list)
    runs: list[LearnedRun] = Field(default_factory=list)


class LearnedStore(Protocol):
    async def learned(self, user_id: UUID) -> LearnedReport: ...



class PostgresLearnedStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def learned(self, user_id: UUID) -> LearnedReport:
        async with self._pool.connection(timeout=self._timeout) as connection:
            await connection.execute("set local role personal_os_api")
            fixes = await self._fixes(connection, user_id)
            branches = await self._branches(connection, user_id)
            unsolved = await self._unsolved(connection, user_id)
            runs = await self._runs(connection, user_id)
        return LearnedReport(
            fixes=fixes, branches=branches, unsolved=unsolved, runs=runs,
        )

    async def _fixes(
        self, connection: Any, user_id: UUID
    ) -> list[LearnedFix]:
        """Every code change, newest first, folded to each file's status."""
        cursor = await connection.execute(
            f"""
            select {CHANGE_COLUMNS} from app.jobs_changes
            where user_id = %s and subject = 'code'
            order by created_at asc
            """,
            (user_id,),
        )
        by_file: dict[str, dict[str, Any]] = {}
        for row in await cursor.fetchall():
            change = dict(row)
            detail = change.get("after") or {}
            files = detail.get("files") or (
                [detail["file"]] if detail.get("file") else []
            )
            file_path = str(files[0]) if files else ""
            # A trial outcome (proven/retired) applies to the file it names;
            # a kept fix applies to its files.
            outcome = str(detail.get("outcome") or "")
            action = str(change.get("action") or "")
            if not file_path:
                continue
            current = by_file.setdefault(file_path, {"latest": change, "status": "trial"})
            if outcome == "proven":
                current["status"] = "proven"
            elif outcome == "retired" or action == "retire":
                current["status"] = "retired"
            elif action == "create":
                current["status"] = "trial"
        result = []
        for file_path, entry in by_file.items():
            change = entry["latest"]
            detail = change.get("after") or {}
            result.append(LearnedFix(
                change_id=change["id"],
                commit=str(change.get("subject_id") or ""),
                file=file_path,
                cause=detail.get("cause"),
                question=detail.get("question"),
                site=_site_of(file_path),
                issue_id=change.get("failure_issue_id"),
                checks=detail.get("checks"),
                probe=detail.get("probe"),
                summary=detail.get("summary"),
                status=entry["status"],
                created_at=change["created_at"],
            ))
        return result

    async def _branches(
        self, connection: Any, user_id: UUID
    ) -> list[LearnedBranch]:
        cursor = await connection.execute(
            """
            select id, fact_key, expression, question_label, created_at
            from app.jobs_patterns
            where user_id = %s and status = 'active' and origin = 'model'
            order by created_at desc
            """,
            (user_id,),
        )
        return [
            LearnedBranch(
                pattern_id=row["id"], fact_key=row.get("fact_key"),
                expression=row.get("expression"),
                question=row.get("question_label"),
                created_at=row["created_at"],
            )
            for row in await cursor.fetchall()
        ]

    async def _unsolved(
        self, connection: Any, user_id: UUID
    ) -> list[LearnedUnsolved]:
        cursor = await connection.execute(
            """
            select id, signature, category, field_label, verification_notes,
                   created_at
            from app.jobs_autofill_failure_issues
            where user_id = %s and status = 'unsolved'
              and signature like '%|problem|%'
            order by created_at desc
            """,
            (user_id,),
        )
        result = []
        for row in await cursor.fetchall():
            signature = str(row.get("signature") or "")
            key = signature.rsplit("problem|", 1)[-1]
            parts = key.split("|")
            result.append(LearnedUnsolved(
                issue_id=row["id"],
                cause=parts[0] if parts else str(row.get("category") or ""),
                site=parts[1] if len(parts) > 1 else None,
                label=row.get("field_label"),
                attempts=row.get("verification_notes"),
                created_at=row["created_at"],
            ))
        return result

    async def _runs(
        self, connection: Any, user_id: UUID
    ) -> list[LearnedRun]:
        cursor = await connection.execute(
            """
            select id, application_url, final_url, created_at, model_calls,
                   model_usage
            from app.jobs_autofill_runs
            where user_id = %s
            order by created_at desc
            limit 20
            """,
            (user_id,),
        )
        return [
            LearnedRun(
                run_id=row["id"],
                site=_site_of(str(row.get("final_url") or row.get("application_url") or "")),
                started_at=row["created_at"],
                model_calls=int(row.get("model_calls") or 0),
                model_usage=row.get("model_usage") or {},
            )
            for row in await cursor.fetchall()
        ]

def _site_of(path: str) -> str | None:
    """A learned file's site from its path or name."""
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.replace("_", ".") or None


def get_learned_store(request: Request) -> PostgresLearnedStore:
    settings: Settings = request.app.state.settings
    return PostgresLearnedStore(
        request.app.state.pool, settings.database_pool_timeout_seconds
    )


router = APIRouter(prefix="/v1/jobs", tags=["learned"])
Store = Annotated[LearnedStore, Depends(get_learned_store)]


@router.get("/learned", response_model=LearnedReport)
async def learned_report(principal: CurrentPrincipal, store: Store) -> LearnedReport:
    return await store.learned(principal.user_id)



