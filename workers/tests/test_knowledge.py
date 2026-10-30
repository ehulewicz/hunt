import httpx
import pytest

from personal_os_worker.jobs import knowledge


@pytest.mark.parametrize("error", [RuntimeError("Sign in again"), httpx.ConnectError("Offline")])
def test_replay_connection_failures_are_distinct_from_regressions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception,
) -> None:
    def unavailable():  # type: ignore[no-untyped-def]
        raise error

    monkeypatch.setattr(knowledge, "connected", unavailable)
    assert knowledge.run_replay() == 2
    assert "could not complete" in capsys.readouterr().out


EMAIL = {"id": "e-email", "kind": "expression", "expression": r"\bemail\b",
         "fact_key": "contact.email", "resolver_id": "expression:contact.email",
         "status": "active"}


def fact(key: str, value: object) -> dict[str, object]:
    return {"key": key, "label": key, "value": value, "value_type": "text"}


class PendingFacts:
    """The API as a run meets it after you set facts on the web."""

    def __init__(self, changes: list[dict[str, object]], facts: list[dict[str, object]]) -> None:
        self.pending = changes
        self.stored = facts
        self.resolved: dict[str, dict[str, int]] = {}
        self.refuse: set[str] = set()
        self.filters: dict[str, object] = {}

    def changes(self, **filters: object) -> list[dict[str, object]]:
        self.filters = filters
        return self.pending

    def career(self) -> None:
        raise RuntimeError("no career record")

    def facts(self) -> list[dict[str, object]]:
        return self.stored

    def patterns(self, status: str) -> list[dict[str, object]]:
        return [EMAIL]

    def sightings(self) -> list[dict[str, object]]:
        # Accepted before fill inputs were saved: no knowledge of its own.
        return [{"question": "Email", "question_key": "email", "kind": "text_entry",
                 "attributes": {"tag": "input", "type": "text"}, "control": "text",
                 "answer": "ethan@old.example", "fact_key": "contact.email",
                 "knowledge": None}]

    def resolve_change(self, change_id: str, verification: dict[str, int]) -> dict[str, object]:
        if change_id in self.refuse:
            request = httpx.Request("PATCH", f"https://api.test/{change_id}")
            raise httpx.HTTPStatusError(
                "gone", request=request, response=httpx.Response(404, request=request)
            )
        self.resolved[change_id] = verification
        return {}


def test_a_fact_set_on_the_web_is_replayed_from_the_value_it_replaced() -> None:
    old, new = "ethan@old.example", "ethan@new.example"
    api = PendingFacts(
        # Newest first, as the API lists them.
        [{"id": "agrees", "subject_id": "contact.email",
          "before": fact("contact.email", new), "after": fact("contact.email", new)},
         {"id": "deleted", "subject_id": "identity.nickname",
          "before": fact("identity.nickname", "E"), "after": None},
         {"id": "contradicts", "subject_id": "contact.email",
          "before": fact("contact.email", old), "after": fact("contact.email", new)}],
        # The fact is already saved with its new value.
        [fact("contact.email", new)],
    )

    assert knowledge.resolve_pending_facts(api) == 1  # type: ignore[arg-type]

    assert api.filters == {"subject": "fact", "status": "pending", "limit": 500}
    # From the value it replaced, the accepted answer is the baseline: the change
    # is a regression, not an answer with no known baseline.
    assert api.resolved == {"contradicts": {"sampled": 1, "failed": 1},
                            "agrees": {"sampled": 0, "failed": 0}}


def test_pending_facts_wait_when_the_answers_cannot_be_read() -> None:
    api = PendingFacts([{"id": "c", "subject_id": "contact.email", "before": None,
                         "after": fact("contact.email", "x@example.com")}], [])

    def unreadable() -> list[dict[str, object]]:
        raise httpx.ConnectError("offline")

    api.sightings = unreadable  # type: ignore[method-assign]
    assert knowledge.resolve_pending_facts(api) == 0  # type: ignore[arg-type]
    assert api.resolved == {}


def test_a_change_the_api_will_not_resolve_stays_pending_and_the_rest_go_on() -> None:
    api = PendingFacts(
        [{"id": "later", "subject_id": "contact.email", "before": None,
          "after": fact("contact.email", "ethan@old.example")},
         {"id": "refused", "subject_id": "contact.email", "before": None,
          "after": fact("contact.email", "x@example.com")}],
        [fact("contact.email", "ethan@old.example")],
    )
    api.refuse = {"refused"}
    assert knowledge.resolve_pending_facts(api) == 0  # type: ignore[arg-type]
    assert list(api.resolved) == ["later"]


def test_replay_checks_pending_facts_first_but_the_fixers_check_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    resolved: list[object] = []
    argv_seen: list[list[str]] = []
    monkeypatch.setattr(knowledge, "connected", lambda: (None, None))
    monkeypatch.setattr(knowledge, "WorkerApi", lambda *_: type("Api", (), {
        "close": lambda self: None})())
    monkeypatch.setattr(knowledge, "resolve_pending_facts", lambda api: resolved.append(api) or 2)
    monkeypatch.setattr(knowledge.replay, "main", lambda argv: argv_seen.append(argv) or 0)

    assert knowledge.run_replay(json_line=True) == 0
    assert resolved == []
    assert knowledge.run_replay() == 0
    assert len(resolved) == 1
    assert argv_seen == [["--json"], []]
    assert "2 fact change(s) since the last check now disagree" in capsys.readouterr().out
