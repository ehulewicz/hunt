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


async def test_facts_are_ranked_by_label_and_never_send_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from pydantic import SecretStr

    from personal_os_backend import jobs_search
    from personal_os_backend.jobs_search import FactLabel, FactRanking, rank_facts

    axes = {"How many years have you programmed?": 0, "Years of experience": 0,
            "Graduation date": 1, "Phone number": 2}
    sent: list[str] = []

    async def embed(_client: object, _key: str, texts: list[str]) -> list[list[float]]:
        sent.extend(texts)
        return [[1.0 if index == axes[text] else 0.0 for index in range(3)] + [0.0] * 1533
                for text in texts]

    monkeypatch.setattr(jobs_search, "embed_questions", embed)
    monkeypatch.setattr(jobs_search, "_VECTORS", {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(openai_api_key=SecretStr("test-key")))))
    item = FactRanking(
        questions=["How many years have you programmed?"],
        facts=[FactLabel(key="education.graduation", label="Graduation date"),
               FactLabel(key="experience.years", label="Years of experience"),
               FactLabel(key="contact.phone", label="Phone number")],
        limit=2,
    )
    [ranked] = await rank_facts(request, item)  # type: ignore[arg-type]
    assert ranked.keys[0] == "experience.years" and len(ranked.keys) == 2
    assert set(sent) == set(axes)
    # Asked again, nothing is embedded twice.
    sent.clear()
    await rank_facts(request, item)  # type: ignore[arg-type]
    assert sent == []
