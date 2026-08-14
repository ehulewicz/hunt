from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx
from playwright.sync_api import Error as PlaywrightError

from personal_os_worker.api import WorkerApi
from personal_os_worker.browser import VisibleBrowser
from personal_os_worker.claude_tailor import tailor_resume
from personal_os_worker.config import WorkerConfig
from personal_os_worker.resume_guard import UnsafeResumeChange
from personal_os_worker.resume_pdf import compile_resume


class LeaseLost(RuntimeError):
    pass


class HeartbeatClient(Protocol):
    def heartbeat(self, task_id: UUID, worker_id: str) -> None: ...


class LeaseHeartbeat:
    def __init__(
        self,
        api: HeartbeatClient,
        task_id: UUID,
        worker_id: str,
        *,
        interval_seconds: float = 60,
    ) -> None:
        self._api = api
        self._task_id = task_id
        self._worker_id = worker_id
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> LeaseHeartbeat:
        try:
            self._api.heartbeat(self._task_id, self._worker_id)
        except httpx.HTTPError:
            self._lost.set()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self._interval_seconds * 2, 1))

    def assert_active(self) -> None:
        if self._lost.is_set():
            raise LeaseLost("Worker task lease is no longer active")

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._api.heartbeat(self._task_id, self._worker_id)
            except httpx.HTTPError:
                self._lost.set()
                return


def is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 425, 429} or error.response.status_code >= 500
    return isinstance(
        error,
        (httpx.TransportError, PlaywrightError, subprocess.TimeoutExpired),
    )


class ClaimClient(Protocol):
    def claim(self, worker_id: str) -> dict[str, Any] | None: ...


def claim_with_backoff(
    api: ClaimClient,
    worker_id: str,
    *,
    once: bool,
    sleeper: Any = time.sleep,
) -> dict[str, Any] | None:
    delay_seconds = 1.0
    while True:
        try:
            return api.claim(worker_id)
        except httpx.HTTPError as error:
            if once or not is_retryable(error):
                raise
            print(
                f"Worker API unavailable; retrying in {delay_seconds:g}s.",
                flush=True,
            )
            sleeper(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 30.0)


def run_worker(config: WorkerConfig, *, once: bool = False) -> int:
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    api = WorkerApi(config)
    try:
        while True:
            task = claim_with_backoff(api, worker_id, once=once)
            if task is None:
                if once:
                    return 0
                time.sleep(5)
                continue
            process_task(api, config, worker_id, task)
            if once:
                return 0
    finally:
        api.close()


def process_task(
    api: WorkerApi, config: WorkerConfig, worker_id: str, task: dict[str, Any]
) -> None:
    task_id = UUID(str(task["id"]))
    try:
        payload = task.get("payload")
        if not isinstance(payload, dict) or "application_id" not in payload:
            raise RuntimeError("Application task payload is invalid")
        application_id = UUID(str(payload["application_id"]))
        bundle = api.bundle(application_id)
        output_directory = (
            Path.home()
            / ".local"
            / "share"
            / "personal-os"
            / "documents"
            / str(application_id)
            / str(task_id)
        )
        with LeaseHeartbeat(api, task_id, worker_id) as lease:
            lease.assert_active()
            if task.get("task_type") == "prepare_application":
                with VisibleBrowser(config.cdp_url) as browser:
                    capture = browser.capture_application(str(bundle["application_url"]))
                lease.assert_active()
                validation, claude_result = tailor_resume(
                    bundle["main_resume"],
                    job_description=str(capture["description"]),
                    profile=bundle["profile"],
                    approved_answers=bundle["approved_answers"],
                )
                lease.assert_active()
                pdf = compile_resume(validation.candidate, output_directory)
                lease.assert_active()
                storage_path = api.upload_pdf(task_id, worker_id, pdf)
                lease.assert_active()
                result = {
                    "application_id": str(application_id),
                    "captured_url": capture["url"],
                    "posting_description": capture["description"],
                    "form_fields": capture["fields"],
                    "tailored_resume": validation.candidate,
                    "storage_path": storage_path,
                    "resume_patch": claude_result,
                    "changed_word_ratio": validation.changed_word_ratio,
                    "changed_paths": list(validation.changed_paths),
                    "approval_required": True,
                    "submitted": False,
                }
            elif task.get("task_type") == "autofill_application":
                resume = api.download_resume(application_id, output_directory / "approved.pdf")
                lease.assert_active()
                with VisibleBrowser(config.cdp_url) as browser:
                    result = browser.autofill_application(
                        str(bundle["application_url"]),
                        profile=bundle["profile"],
                        approved_answers=bundle["approved_answers"],
                        resume_path=resume,
                        assert_active=lease.assert_active,
                    )
                lease.assert_active()
                result["application_id"] = str(application_id)
            else:
                raise RuntimeError(f"Unsupported task type: {task.get('task_type')}")
            api.complete(task_id, worker_id, result)
    except LeaseLost:
        return
    except (
        httpx.HTTPError,
        OSError,
        PlaywrightError,
        RuntimeError,
        subprocess.SubprocessError,
        UnsafeResumeChange,
        ValueError,
    ) as error:
        try:
            api.fail(task_id, worker_id, str(error), retry=is_retryable(error))
        except httpx.HTTPError:
            return
