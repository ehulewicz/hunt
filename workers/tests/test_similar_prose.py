from typing import Any

import httpx
from test_autofill_session import FakeApi, audit_session


class SearchApi(FakeApi):
    def __init__(self) -> None:
        super().__init__()
        self.searches: list[tuple[str, bool]] = []

    def similar_questions(self, question: str, *, prose_only: bool) -> list[dict[str, Any]]:
        self.searches.append((question, prose_only))
        return [{"question": "Why Figma?", "prose": [
            {"approved": True, "text": "I enjoy building collaborative tools."},
            {"approved": False, "text": "Unapproved draft."},
        ]}]


def test_semantic_references_are_approved_and_cached_for_the_run() -> None:
    api = SearchApi()
    session = audit_session(None, facts={}, api=api)
    assert session.references("Why this employer?") == [
        {"question": "Why Figma?", "answer_text": "I enjoy building collaborative tools."},
    ]
    assert session.references("Why this employer?") == session.references("Why this employer?")
    assert api.searches == [("Why this employer?", True)]


def test_semantic_search_outage_preserves_the_wording_fallback() -> None:
    class OfflineApi(SearchApi):
        def similar_questions(self, question: str, *, prose_only: bool) -> list[dict[str, Any]]:
            raise httpx.ConnectError("Offline")

    session = audit_session(None, facts={}, api=OfflineApi())
    assert session.references("Why this employer?") == []
