"""Answers: questions, patterns, prose, the archive, and the Inbox over them.

Knowledge starts empty and is learned (design §0). A question is recorded the
first time a fill event meets it. A pattern connects a short-form question to a
fact; only `active` patterns are read when filling. A model never fills: the
worker checks the fact a model names and saves the pattern active with origin
`model` (R67), and the Inbox lists a pattern only while it waits on you. Prose is
long-form writing saved from submitted applications, reused once approved. The
old answer library is the archive: readable, never written, and kept only for
the facts session — no application reads it.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from psycopg_pool import AsyncConnectionPool

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings
from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_dispositions import QuestionsMixin, QuestionsStore
from personal_os_backend.jobs_inbox import Inbox, InboxMixin, InboxStore, ProseGap
from personal_os_backend.jobs_patterns import (
    Pattern,
    PatternCreate,
    PatternRefused,
    PatternsMixin,
    PatternsStore,
    PatternStatus,
    PatternUpdate,
    _model_refusal,
    active_patterns,
    runaway_expression,
)
from personal_os_backend.jobs_prose import (
    PROSE_SELECT,
    ArchivedAnswer,
    Prose,
    ProseCreate,
    ProseMixin,
    ProseSpan,
    ProseStore,
    ProseUpdate,
    check_spans,
    stripped_spans,
)
from personal_os_backend.jobs_questions import (
    Disposition,
    Question,
    QuestionUpdate,
    answered_by_expression,
    asks_a_question,
    normalize_answer_key,
    upsert_question,
)

__all__ = [
    "PROSE_SELECT",
    "ArchivedAnswer",
    "Inbox",
    "Pattern",
    "PatternCreate",
    "PatternRefused",
    "PatternUpdate",
    "PostgresAnswerStore",
    "Prose",
    "ProseGap",
    "ProseSpan",
    "ProseUpdate",
    "Question",
    "QuestionUpdate",
    "_model_refusal",
    "active_patterns",
    "answered_by_expression",
    "asks_a_question",
    "check_spans",
    "get_answer_store",
    "normalize_answer_key",
    "router",
    "runaway_expression",
    "stripped_spans",
    "upsert_question",
]


class AnswerStore(QuestionsStore, PatternsStore, ProseStore, InboxStore, Protocol): ...


class PostgresAnswerStore(QuestionsMixin, PatternsMixin, ProseMixin, InboxMixin, StoreBase):
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout


async def get_answer_store(request: Request) -> AnswerStore:
    settings: Settings = request.app.state.settings
    return PostgresAnswerStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs", tags=["answers"])
Store = Annotated[AnswerStore, Depends(get_answer_store)]


@router.get("/inbox", response_model=Inbox)
async def get_inbox(principal: CurrentPrincipal, store: Store) -> Inbox:
    return await store.inbox(principal.user_id)


@router.get("/questions", response_model=list[Question])
async def list_questions(
    principal: CurrentPrincipal,
    store: Store,
    disposition: Annotated[Disposition | None, Query()] = None,
) -> list[Question]:
    return await store.list_questions(principal.user_id, disposition)


@router.patch("/questions/{question_id}", response_model=Question)
async def update_question(
    question_id: UUID, item: QuestionUpdate, principal: CurrentPrincipal, store: Store
) -> Question:
    question = await store.update_question(principal.user_id, question_id, item)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return question


@router.get("/patterns", response_model=list[Pattern])
async def list_patterns(
    principal: CurrentPrincipal,
    store: Store,
    statuses: Annotated[list[PatternStatus] | None, Query(alias="status")] = None,
) -> list[Pattern]:
    return await store.list_patterns(principal.user_id, statuses or ["active", "candidate"])


@router.post("/patterns", response_model=Pattern, status_code=status.HTTP_201_CREATED)
async def create_pattern(item: PatternCreate, principal: CurrentPrincipal, store: Store) -> Pattern:
    try:
        pattern = await store.create_pattern(principal.user_id, item)
    except PatternRefused as refusal:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refusal)) from None
    if pattern is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return pattern


@router.patch("/patterns/{pattern_id}", response_model=Pattern)
async def update_pattern(
    pattern_id: UUID, item: PatternUpdate, principal: CurrentPrincipal, store: Store
) -> Pattern:
    pattern = await store.update_pattern(principal.user_id, pattern_id, item)
    if pattern is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pattern not found")
    return pattern


@router.get("/prose", response_model=list[Prose])
async def list_prose(principal: CurrentPrincipal, store: Store) -> list[Prose]:
    return await store.list_prose(principal.user_id)


@router.post("/prose", response_model=Prose, status_code=status.HTTP_201_CREATED)
async def create_prose(item: ProseCreate, principal: CurrentPrincipal, store: Store) -> Prose:
    return await store.create_prose(principal.user_id, item)


@router.patch("/prose/{prose_id}", response_model=Prose)
async def update_prose(
    prose_id: UUID, item: ProseUpdate, principal: CurrentPrincipal, store: Store
) -> Prose:
    prose = await store.update_prose(principal.user_id, prose_id, item)
    if prose is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prose not found")
    return prose


@router.delete("/prose/{prose_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prose(prose_id: UUID, principal: CurrentPrincipal, store: Store) -> Response:
    if not await store.delete_prose(principal.user_id, prose_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prose not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/archive/answers", response_model=list[ArchivedAnswer])
async def list_archive(principal: CurrentPrincipal, store: Store) -> list[ArchivedAnswer]:
    return await store.list_archive(principal.user_id)
