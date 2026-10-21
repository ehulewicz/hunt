"""Question similarity, using cached OpenAI vectors in the private Jobs schema.

Only question labels and fact labels are embedded. FastAPI owns both the
provider credential and database access; neither answers nor fact values go to
the embedding API. Ranking facts for a question orders the facts a model is
shown, closest first; it never hides one.
"""

from __future__ import annotations

import json
import math
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.jobs_answers import PROSE_SELECT, Prose, normalize_answer_key

MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
THRESHOLD = 0.65
BATCH_SIZE = 128


async def embed_questions(
    client: httpx.AsyncClient, key: str, questions: list[str],
) -> list[list[float]]:
    response = await client.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL, "dimensions": DIMENSIONS, "encoding_format": "float",
              "input": questions},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(questions):
        raise ValueError("The embedding response has the wrong number of vectors")
    vectors: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("The embedding response contains an invalid item")
        index, vector = item.get("index"), item.get("embedding")
        if (
            not isinstance(index, int) or isinstance(index, bool)
            or not 0 <= index < len(questions) or index in vectors
            or not isinstance(vector, list) or len(vector) != DIMENSIONS
            or any(isinstance(n, bool) or not isinstance(n, int | float)
                   or not math.isfinite(n) for n in vector)
            or not any(vector)
        ):
            raise ValueError("The embedding response contains an invalid vector")
        vectors[index] = vector
    return [vectors[index] for index in range(len(questions))]


class SimilarityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    threshold: float = Field(default=THRESHOLD, ge=0, le=1)
    limit: int = Field(default=5, ge=1, le=20)
    prose_only: bool = False


class SimilarQuestion(BaseModel):
    question_id: UUID
    question: str
    similarity: float
    prose: list[Prose] = Field(default_factory=list)


async def search_questions(
    request: Request, user_id: UUID, query: SimilarityQuery,
) -> list[SimilarQuestion]:
    settings = request.app.state.settings
    if settings.openai_api_key is None:
        raise HTTPException(503, "Question similarity is not configured")
    pool = request.app.state.pool
    # Read and release the connection before calling the external provider.
    async with (
        pool.connection(timeout=settings.database_pool_timeout_seconds) as connection,
        connection.transaction(),
    ):
        await connection.execute("set local role personal_os_api")
        cursor = await connection.execute(
            "select id, normalized_key, embedding::text as embedding "
            "from app.jobs_questions where user_id = %s and normalized_key = %s",
            (user_id, normalize_answer_key(query.question)),
        )
        cached = await cursor.fetchone()
        cursor = await connection.execute(
            "select id, sample_label from app.jobs_questions "
            "where user_id = %s and embedding is null order by last_seen_at desc limit 2000",
            (user_id,),
        )
        missing = await cursor.fetchall()
    pending = [str(row["sample_label"]) for row in missing]
    query_index = next(
        (index for index, row in enumerate(missing) if cached and row["id"] == cached["id"]),
        None,
    )
    if cached and cached["embedding"]:
        query_vector = str(cached["embedding"])
    else:
        query_vector = ""
        if query_index is None:
            query_index = len(pending)
            pending.append(query.question)
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for start in range(0, len(pending), BATCH_SIZE):
            vectors.extend(await embed_questions(
                client, settings.openai_api_key.get_secret_value(),
                pending[start:start + BATCH_SIZE],
            ))
    if not query_vector and query_index is not None:
        query_vector = json.dumps(vectors[query_index])
    async with (
        pool.connection(timeout=settings.database_pool_timeout_seconds) as connection,
        connection.transaction(),
    ):
        await connection.execute("set local role personal_os_api")
        for row, vector in zip(missing, vectors, strict=False):
            await connection.execute(
                "update app.jobs_questions set embedding = %s::extensions.vector "
                "where id = %s and user_id = %s and embedding is null",
                (json.dumps(vector), row["id"], user_id),
            )
        cursor = await connection.execute(
            """
            select id as question_id, sample_label as question,
                   1 - (embedding OPERATOR(extensions.<=>) %s::extensions.vector) as similarity
            from app.jobs_questions question
            where user_id = %s and embedding is not null
              and (not %s or exists (
                select 1 from app.jobs_prose prose
                where prose.question_id = question.id and prose.user_id = question.user_id
                  and prose.approved
              ))
              and 1 - (embedding OPERATOR(extensions.<=>) %s::extensions.vector) >= %s
            order by embedding OPERATOR(extensions.<=>) %s::extensions.vector, id
            limit %s
            """,
            (query_vector, user_id, query.prose_only, query_vector, query.threshold,
             query_vector, query.limit),
        )
        matches = [SimilarQuestion.model_validate(row) for row in await cursor.fetchall()]
        if matches:
            cursor = await connection.execute(
                f"{PROSE_SELECT} where prose.user_id = %s and prose.approved "
                "and prose.question_id = any(%s) order by prose.updated_at desc",
                (user_id, [match.question_id for match in matches]),
            )
            prose = [Prose.model_validate(row) for row in await cursor.fetchall()]
            for match in matches:
                match.prose = [item for item in prose if item.question_id == match.question_id]
    return matches


class FactLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=3, max_length=200, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    label: str = Field(min_length=1, max_length=300)


class FactRanking(BaseModel):
    """Questions to rank facts for, by the facts' labels: never their values."""

    model_config = ConfigDict(extra="forbid")

    questions: list[str] = Field(min_length=1, max_length=50)
    facts: list[FactLabel] = Field(min_length=1, max_length=1000)
    limit: int = Field(default=15, ge=1, le=100)


class RankedFacts(BaseModel):
    question: str
    keys: list[str]


# Label vectors, kept in memory: a fact catalog's labels rarely change, and a
# page step asks for the same ones again. Bounded so it cannot grow unchecked.
_VECTORS: dict[str, list[float]] = {}
VECTOR_CACHE_LIMIT = 5000


async def rank_facts(request: Request, item: FactRanking) -> list[RankedFacts]:
    settings = request.app.state.settings
    if settings.openai_api_key is None:
        raise HTTPException(503, "Fact ranking is not configured")
    texts = list(dict.fromkeys([*item.questions, *(fact.label for fact in item.facts)]))
    pending = [text for text in texts if text not in _VECTORS]
    async with httpx.AsyncClient(timeout=30) as client:
        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start:start + BATCH_SIZE]
            vectors = await embed_questions(
                client, settings.openai_api_key.get_secret_value(), batch
            )
            if len(_VECTORS) + len(batch) > VECTOR_CACHE_LIMIT:
                _VECTORS.clear()
            _VECTORS.update(zip(batch, vectors, strict=True))

    def similarity(left: list[float], right: list[float]) -> float:
        # The provider's vectors are unit length: their dot product is the cosine.
        return sum(a * b for a, b in zip(left, right, strict=True))

    rankings: list[RankedFacts] = []
    for question in item.questions:
        target = _VECTORS[question]
        scored = sorted(
            item.facts, key=lambda fact: (-similarity(target, _VECTORS[fact.label]), fact.key)
        )
        rankings.append(RankedFacts(
            question=question, keys=[fact.key for fact in scored[:item.limit]]
        ))
    return rankings


router = APIRouter(prefix="/v1/jobs/questions", tags=["questions"])


@router.post("/rank-facts", response_model=list[RankedFacts])
async def rank_facts_for_questions(
    item: FactRanking, principal: CurrentPrincipal, request: Request,
) -> list[RankedFacts]:
    try:
        return await rank_facts(request, item)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise HTTPException(503, "Fact ranking is temporarily unavailable") from error


@router.post("/similar", response_model=list[SimilarQuestion])
async def similar_questions(
    item: SimilarityQuery, principal: CurrentPrincipal, request: Request,
) -> list[SimilarQuestion]:
    try:
        return await search_questions(request, principal.user_id, item)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        # Provider responses can include request metadata; do not expose them.
        raise HTTPException(503, "Question similarity is temporarily unavailable") from error
