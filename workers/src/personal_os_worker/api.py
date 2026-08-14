from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from personal_os_worker.config import WorkerConfig


class WorkerApi:
    def __init__(self, config: WorkerConfig) -> None:
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        response = self._client.post("/v1/jobs/worker/tasks/claim", json={"worker_id": worker_id})
        response.raise_for_status()
        payload = response.json()
        task = payload.get("task")
        return task if isinstance(task, dict) else None

    def bundle(self, application_id: UUID) -> dict[str, Any]:
        response = self._client.get(f"/v1/jobs/worker/applications/{application_id}/bundle")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Application bundle response was invalid")
        return payload

    def heartbeat(self, task_id: UUID, worker_id: str) -> None:
        response = self._client.post(
            f"/v1/jobs/worker/tasks/{task_id}/heartbeat",
            json={"worker_id": worker_id},
        )
        response.raise_for_status()

    def upload_pdf(self, task_id: UUID, worker_id: str, path: Path) -> str:
        with path.open("rb") as handle:
            response = self._client.post(
                f"/v1/jobs/worker/tasks/{task_id}/pdf",
                files={"document": (path.name, handle, "application/pdf")},
                data={"worker_id": worker_id},
            )
        response.raise_for_status()
        storage_path = response.json().get("storage_path")
        if not isinstance(storage_path, str):
            raise RuntimeError("PDF upload did not return a storage path")
        return storage_path

    def download_resume(self, application_id: UUID, destination: Path) -> Path:
        response = self._client.get(f"/v1/jobs/worker/applications/{application_id}/resume")
        response.raise_for_status()
        if not response.content.startswith(b"%PDF-"):
            raise RuntimeError("Approved resume response was not a PDF")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def doctor(self) -> None:
        response = self._client.get("/v1/jobs/worker/doctor")
        response.raise_for_status()

    def complete(self, task_id: UUID, worker_id: str, result: dict[str, Any]) -> None:
        response = self._client.post(
            f"/v1/jobs/worker/tasks/{task_id}/complete",
            json={"worker_id": worker_id, "result": result},
        )
        response.raise_for_status()

    def fail(
        self,
        task_id: UUID,
        worker_id: str,
        error_message: str,
        *,
        retry: bool,
    ) -> None:
        response = self._client.post(
            f"/v1/jobs/worker/tasks/{task_id}/fail",
            json={
                "worker_id": worker_id,
                "error_message": error_message[:4000],
                "retry": retry,
            },
        )
        response.raise_for_status()
