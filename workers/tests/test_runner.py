import subprocess
import time
from uuid import UUID

import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError

from personal_os_worker.runner import (
    LeaseHeartbeat,
    LeaseLost,
    claim_with_backoff,
    is_retryable,
)

TASK_ID = UUID("22222222-2222-4222-8222-222222222222")


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
