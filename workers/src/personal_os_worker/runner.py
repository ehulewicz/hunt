from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from personal_os_worker.api import WorkerApi
from personal_os_worker.browser import VisibleBrowser
from personal_os_worker.claude_tailor import tailor_resume
from personal_os_worker.config import WorkerConfig
from personal_os_worker.resume_guard import UnsafeResumeChange
from personal_os_worker.resume_pdf import compile_resume


def run_worker(config: WorkerConfig, *, once: bool = False) -> int:
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    api = WorkerApi(config)
    try:
        while True:
            task = api.claim(worker_id)
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
        if task.get("task_type") != "prepare_application":
            raise RuntimeError(f"Unsupported task type: {task.get('task_type')}")
        payload = task.get("payload")
        if not isinstance(payload, dict) or "application_id" not in payload:
            raise RuntimeError("Application task payload is invalid")
        application_id = UUID(str(payload["application_id"]))
        bundle = api.bundle(application_id)
        api.heartbeat(task_id, worker_id)
        with VisibleBrowser(config.cdp_url) as browser:
            capture = browser.capture_application(str(bundle["application_url"]))
        api.heartbeat(task_id, worker_id)
        validation, claude_result = tailor_resume(
            bundle["main_resume"],
            job_description=str(capture["description"]),
            profile=bundle["profile"],
            approved_answers=bundle["approved_answers"],
        )
        output_directory = (
            Path.home()
            / ".local"
            / "share"
            / "personal-os"
            / "documents"
            / str(application_id)
            / str(task_id)
        )
        pdf = compile_resume(validation.candidate, output_directory)
        storage_path = api.upload_pdf(task_id, pdf)
        api.complete(
            task_id,
            worker_id,
            {
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
            },
        )
    except (httpx.HTTPError, RuntimeError, UnsafeResumeChange, ValueError) as error:
        api.fail(task_id, worker_id, str(error), retry=False)
