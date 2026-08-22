from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from personal_os_worker.config import CliConfig


class WorkerApi:
    """Client for the local API, authenticated with the signed-in Google session."""

    def __init__(self, config: CliConfig, access_token: str) -> None:
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def listings(
        self,
        *,
        category: str | None = None,
        locations: list[str] | None = None,
        role_families: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("limit", limit),
            ("offset", offset),
        ]
        if category:
            params.append(("category", category))
        for location in locations or []:
            params.append(("locations", location))
        for family in role_families or []:
            params.append(("role_families", family))
        response = self._client.get("/v1/jobs/listings", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Listing response was invalid")
        return payload

    def sync_listings(self) -> dict[str, Any]:
        response = self._client.post("/v1/jobs/listings/sync")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Listing sync response was invalid")
        return payload

    def profile(self) -> dict[str, Any]:
        response = self._client.get("/v1/jobs/profile")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Profile response was invalid")
        return payload

    def answers(self) -> list[dict[str, Any]]:
        response = self._client.get("/v1/jobs/answers")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def create_answer(
        self, *, question: str, answer_text: str, approved: bool
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs/answers",
            json={"question": question, "answer_text": answer_text, "approved": approved},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Created answer response was invalid")
        return payload

    def update_answer(self, answer_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        response = self._client.patch(f"/v1/jobs/answers/{answer_id}", json=changes)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Updated answer response was invalid")
        return payload

    def applications(self) -> list[dict[str, Any]]:
        response = self._client.get("/v1/jobs/applications")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Application response was invalid")
        return payload

    def batch_prepare(self, posting_ids: list[str], *, tailor: bool = False) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs/applications/batch-prepare",
            json={"posting_ids": posting_ids, "tailor": tailor},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Batch prepare response was invalid")
        return payload

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

    def document(self, document_id: UUID) -> dict[str, Any]:
        response = self._client.get(f"/v1/jobs/worker/documents/{document_id}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Main resume draft response was invalid")
        return payload

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
