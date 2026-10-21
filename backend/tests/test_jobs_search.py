import httpx
import pytest

from personal_os_backend.jobs_search import embed_questions


async def test_embeddings_send_only_question_text_and_keep_response_order() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert body == {"model": "text-embedding-3-small", "dimensions": 1536,
                        "encoding_format": "float", "input": ["Why this company?", "Career goals?"]}
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 1.0] + [0.0] * 1534},
            {"index": 0, "embedding": [1.0] + [0.0] * 1535},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        vectors = await embed_questions(client, "test-key", ["Why this company?", "Career goals?"])
    assert vectors[0][:2] == [1.0, 0.0]
    assert vectors[1][:2] == [0.0, 1.0]


@pytest.mark.parametrize("data", [
    [], [{"index": 0, "embedding": [1.0]}],
    [{"index": 0, "embedding": [0.0] * 1536}],
    [{"index": 0, "embedding": [1.0] * 1536}, {"index": 0, "embedding": [1.0] * 1536}],
])
async def test_malformed_embedding_responses_never_reach_the_database(data: list[object]) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data}),
    )) as client:
        with pytest.raises(ValueError, match="embedding"):
            await embed_questions(client, "test-key", ["Why this company?"])
