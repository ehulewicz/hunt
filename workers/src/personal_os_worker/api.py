from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

import httpx

from personal_os_worker.auth import LoginError, SupabaseAuth, renewed_access_token
from personal_os_worker.config import CliConfig
from personal_os_worker.tables import ReferenceError


class RenewingBearer(httpx.Auth):
    """Bearer auth that renews the token once and retries when the API says 401.

    An apply session can run longer than an access token lives; renewing in
    place means the answers it collected are still saved at the end.
    """

    requires_response_body = False

    def __init__(self, access_token: str, renew: Callable[[], str]) -> None:
        self._token = access_token
        self._renew = renew

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code != 401:
            return
        try:
            self._token = self._renew()
        except LoginError:
            return
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class WorkerApi:
    """Client for the local API, authenticated with the signed-in Google session."""

    def __init__(
        self, config: CliConfig, access_token: str, *, renew: Callable[[], str] | None = None
    ) -> None:
        auth = SupabaseAuth(
            url=config.supabase_url, publishable_key=config.supabase_publishable_key
        )
        self._client = httpx.Client(
            base_url=config.api_url,
            auth=RenewingBearer(access_token, renew or (lambda: renewed_access_token(auth))),
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

    def listing(self, reference: str) -> dict[str, Any]:
        """One listing by its board number or an id prefix; the API does the resolving."""
        response = self._client.get(f"/v1/jobs/listings/{reference}")
        if response.status_code in {404, 409}:
            detail = response.json().get("detail") if response.content else None
            raise ReferenceError(str(detail or f"No listing {reference!r}"))
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

    def answer_review(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        response = self._client.get(
            "/v1/jobs/answer-review", params={"limit": limit, "offset": offset}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Answer review response was invalid")
        return payload

    def answers(self) -> list[dict[str, Any]]:
        response = self._client.get("/v1/jobs/answers")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def create_answer(
        self,
        *,
        question: str,
        answer_text: str,
        approved: bool,
        reuse_policy: str = "exact",
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs/answers",
            json={
                "question": question,
                "answer_text": answer_text,
                "approved": approved,
                "reuse_policy": reuse_policy,
            },
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

    def create_application(self, posting_id: str, *, status: str) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs/applications", json={"posting_id": posting_id, "status": status}
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Created application response was invalid")
        return payload

    def archive_application(self, application_id: str) -> None:
        response = self._client.delete(f"/v1/jobs/applications/{application_id}")
        response.raise_for_status()

    def update_application(self, application_id: str, **fields: Any) -> dict[str, Any]:
        response = self._client.patch(f"/v1/jobs/applications/{application_id}", json=fields)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Application update response was invalid")
        return payload

    def create_autofill_run(
        self, *, posting_id: str, ats: str, adapter_version: str
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs/autofill/runs",
            json={"posting_id": posting_id, "ats": ats, "adapter_version": adapter_version},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Autofill run response was invalid")
        return payload

    def persist_autofill_step(self, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._client.post(f"/v1/jobs/autofill/runs/{run_id}/steps", json=payload)
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, list) else []

    def upload_autofill_artifact(
        self, run_id: str, *, name: str, content: bytes, content_type: str
    ) -> str:
        response = self._client.post(
            f"/v1/jobs/autofill/runs/{run_id}/artifacts",
            files={"artifact": (name, content, content_type)},
        )
        response.raise_for_status()
        path = response.json().get("storage_path")
        if not isinstance(path, str):
            raise RuntimeError("Artifact upload did not return a storage path")
        return path

    def complete_autofill_run(
        self, run_id: str, *, final_url: str, answers: list[dict[str, Any]],
        submitted: bool, application_status: str
    ) -> None:
        response = self._client.post(
            f"/v1/jobs/autofill/runs/{run_id}/finish",
            json={"final_url": final_url, "answers": answers, "submitted": submitted,
                  "application_status": application_status},
        )
        response.raise_for_status()

    def stop_autofill_run(self, run_id: str, *, status: str) -> None:
        response = self._client.post(
            f"/v1/jobs/autofill/runs/{run_id}/stop", json={"status": status}
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    def autofill_failures(self) -> list[dict[str, Any]]:
        response = self._client.get("/v1/jobs/autofill/failures")
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, list) else []

    def autofill_failure(self, issue_id: str) -> dict[str, Any]:
        response = self._client.get(f"/v1/jobs/autofill/failures/{issue_id}")
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Autofill failure response was invalid")
        return value

    def download_autofill_artifact(self, issue_id: str, path: str) -> bytes:
        response = self._client.get(
            f"/v1/jobs/autofill/failures/{issue_id}/artifact", params={"path": path}
        )
        response.raise_for_status()
        return response.content

    def update_autofill_failure(self, issue_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        response = self._client.patch(f"/v1/jobs/autofill/failures/{issue_id}", json=changes)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Updated autofill failure response was invalid")
        return value

    def doctor(self) -> None:
        response = self._client.get("/v1/jobs/worker/doctor")
        response.raise_for_status()
