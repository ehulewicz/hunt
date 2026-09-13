import subprocess
import time
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError

from personal_os_worker.runner import (
    LeaseHeartbeat,
    LeaseLost,
    claim_with_backoff,
    is_retryable,
    prepare_resume_candidate,
    process_resume_preview,
)

TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
DOCUMENT_ID = UUID("33333333-3333-4333-8333-333333333333")


class HeartbeatApi:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def heartbeat(self, task_id: UUID, worker_id: str) -> None:
        self.calls += 1
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise httpx.HTTPStatusError(
                "lease lost",
                request=httpx.Request("POST", "https://example.com/heartbeat"),
                response=httpx.Response(409),
            )


def test_lease_heartbeat_runs_continuously_during_work() -> None:
    api = HeartbeatApi()
    with LeaseHeartbeat(api, TASK_ID, "worker-1", interval_seconds=0.01):
        time.sleep(0.035)

    assert api.calls >= 2


def test_lost_lease_stops_followup_work() -> None:
    api = HeartbeatApi(fail_after=1)
    with LeaseHeartbeat(api, TASK_ID, "worker-1", interval_seconds=0.01) as lease:
        time.sleep(0.02)
        with pytest.raises(LeaseLost):
            lease.assert_active()


def test_only_transient_failures_are_retryable() -> None:
    request = httpx.Request("GET", "https://example.com")
    assert is_retryable(httpx.ConnectError("offline", request=request))
    assert is_retryable(PlaywrightError("Chrome disconnected"))
    assert is_retryable(subprocess.TimeoutExpired("latexmk", 120))
    assert is_retryable(
        httpx.HTTPStatusError("busy", request=request, response=httpx.Response(503))
    )
    assert not is_retryable(
        httpx.HTTPStatusError("bad request", request=request, response=httpx.Response(400))
    )
    assert not is_retryable(ValueError("invalid worker result"))
    assert not is_retryable(FileNotFoundError("latexmk is missing"))


class ClaimApi:
    def __init__(self) -> None:
        self.calls = 0

    def claim(self, worker_id: str) -> None:
        self.calls += 1
        if self.calls < 3:
            raise httpx.ConnectError(
                "offline",
                request=httpx.Request("POST", "https://example.com/claim"),
            )
        return None


def test_claim_waits_through_temporary_api_outage() -> None:
    api = ClaimApi()
    delays: list[float] = []

    assert claim_with_backoff(api, "worker-1", once=False, sleeper=delays.append) is None

    assert api.calls == 3
    assert delays == [1.0, 2.0]


class PreviewApi(HeartbeatApi):
    """Records the preview round trip without touching the network or LaTeX."""

    def __init__(self, resume: dict[str, object]) -> None:
        super().__init__()
        self.resume = resume
        self.completed: dict[str, object] | None = None
        self.uploaded: Path | None = None

    def document(self, document_id: UUID) -> dict[str, object]:
        return self.resume

    def upload_pdf(self, task_id: UUID, worker_id: str, path: Path) -> str:
        self.uploaded = path
        return "user/resumes/document/task.pdf"

    def complete(self, task_id: UUID, worker_id: str, result: dict[str, object]) -> None:
        self.completed = result


def test_resume_preview_compiles_and_uploads_without_an_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = PreviewApi({"contact": {"name": "Candidate"}})
    generated = tmp_path / "resume.pdf"
    generated.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        "personal_os_worker.runner.compile_resume",
        lambda resume, directory: generated,
    )

    process_resume_preview(api, "worker-1", TASK_ID, {"document_id": str(DOCUMENT_ID)})

    assert api.uploaded == generated
    assert api.completed == {
        "document_id": str(DOCUMENT_ID),
        "storage_path": "user/resumes/document/task.pdf",
    }


def test_resume_preview_rejects_a_payload_without_a_document() -> None:
    api = PreviewApi({})
    with pytest.raises(RuntimeError, match="payload is invalid"):
        process_resume_preview(api, "worker-1", TASK_ID, {"application_id": "x"})


def test_application_preparation_always_uses_the_canonical_resume() -> None:
    main_resume = {"contact": {"name": "Candidate"}}
    bundle = {"main_resume": main_resume, "profile": {}, "approved_answers": []}

    assert prepare_resume_candidate(bundle) == main_resume
