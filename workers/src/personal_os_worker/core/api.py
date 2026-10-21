from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

import httpx

from personal_os_worker.core.auth import LoginError, SupabaseAuth, renewed_access_token
from personal_os_worker.core.config import CliConfig
from personal_os_worker.core.tables import ReferenceError


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

    def listing_from_url(
        self, url: str, *, company: str | None = None, title: str | None = None
    ) -> dict[str, Any] | None:
        """The posting at this URL, or a new one named by company and title; None when
        no posting has the URL and it was not named."""
        body = {"url": url, "company": company, "title": title}
        response = self._client.post(
            "/v1/jobs/listings/from-url",
            json={key: value for key, value in body.items() if value},
        )
        if response.status_code == 404:
            return None
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

    def facts(self) -> list[dict[str, Any]]:
        response = self._client.get("/v1/jobs/facts")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Facts response was invalid")
        return payload

    def set_fact(
        self,
        key: str,
        value: Any,
        *,
        label: str | None = None,
        value_type: str | None = None,
        verified: bool = True,
        origin: str | None = None,
        failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Set a fact. `origin` is "inbox" when you answer the Inbox, and `failure`
        names the run and question a fact given mid-run answers."""
        body: dict[str, Any] = {"value": value, "verified": verified}
        optional = {"label": label, "value_type": value_type, "origin": origin, "failure": failure}
        body |= {name: item for name, item in optional.items() if item is not None}
        response = self._client.put(f"/v1/jobs/facts/{key}", json=body)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Fact response was invalid")
        return payload

    def verify_fact(self, key: str) -> dict[str, Any]:
        response = self._client.post(f"/v1/jobs/facts/{key}/verify")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Fact response was invalid")
        return payload

    def delete_fact(self, key: str) -> None:
        response = self._client.delete(f"/v1/jobs/facts/{key}")
        response.raise_for_status()

    def career(self) -> dict[str, Any]:
        """The career record: its rows and the resume document rendered from them."""
        response = self._client.get("/v1/jobs/career")
        if response.status_code == 404:
            raise RuntimeError("No career record yet; run 'os hunt resume import'")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Career response was invalid")
        return payload

    def put_career(self, document: dict[str, Any]) -> dict[str, Any]:
        response = self._client.put("/v1/jobs/career", json=document)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Career response was invalid")
        return payload

    def import_career(self, document: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/v1/jobs/career/import", json=document)
        if response.status_code == 409:
            raise RuntimeError(
                "The career record already exists; edit it with 'os hunt resume edit'"
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Career response was invalid")
        return payload

    def _json(self, response: httpx.Response, kind: str) -> Any:
        if response.status_code == 404:
            detail = response.json().get("detail") if response.content else None
            raise ReferenceError(str(detail or f"{kind} not found"))
        response.raise_for_status()
        return response.json()

    def inbox(self) -> dict[str, Any]:
        payload = self._json(self._client.get("/v1/jobs/inbox"), "Inbox")
        if not isinstance(payload, dict):
            raise RuntimeError("Inbox response was invalid")
        return payload

    def questions(self, *, disposition: str | None = None) -> list[dict[str, Any]]:
        params = {"disposition": disposition} if disposition else None
        payload = self._json(self._client.get("/v1/jobs/questions", params=params), "Questions")
        return payload if isinstance(payload, list) else []

    def update_question(self, question_id: str, *, disposition: str) -> dict[str, Any]:
        payload = self._json(
            self._client.patch(
                f"/v1/jobs/questions/{question_id}", json={"disposition": disposition}
            ),
            "Question",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Question response was invalid")
        return payload

    def patterns(self, *statuses: str) -> list[dict[str, Any]]:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("status", status) for status in statuses
        ]
        payload = self._json(self._client.get("/v1/jobs/patterns", params=params), "Patterns")
        return payload if isinstance(payload, list) else []

    def create_pattern(self, **fields: Any) -> dict[str, Any]:
        payload = self._json(
            self._client.post(
                "/v1/jobs/patterns",
                json={key: value for key, value in fields.items() if value is not None},
            ),
            "Question",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Pattern response was invalid")
        return payload

    def update_pattern(
        self, pattern_id: str, *, status: str, origin: str = "you"
    ) -> dict[str, Any]:
        payload = self._json(
            self._client.patch(
                f"/v1/jobs/patterns/{pattern_id}", json={"status": status, "origin": origin}
            ),
            "Pattern",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Pattern response was invalid")
        return payload

    def changes(self, **filters: str | int) -> list[dict[str, Any]]:
        """The change history, newest first: by subject, subject_id, status, limit."""
        payload = self._json(self._client.get("/v1/jobs/changes", params=filters), "Changes")
        return payload if isinstance(payload, list) else []

    def record_code_change(
        self,
        commit: str,
        *,
        origin: str = "repair",
        failure: dict[str, Any] | None = None,
        verification: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        body = {"commit": commit, "origin": origin, "failure": failure,
                "verification": verification}
        payload = self._json(
            self._client.post(
                "/v1/jobs/changes",
                json={key: value for key, value in body.items() if value is not None},
            ),
            "Change",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Change response was invalid")
        return payload

    def sightings(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Short answers submitted with an application, newest first (`jobs/replay.py`)."""
        params = {"limit": limit} if limit else None
        payload = self._json(self._client.get("/v1/jobs/sightings", params=params), "Sightings")
        return payload if isinstance(payload, list) else []

    def prose(self) -> list[dict[str, Any]]:
        payload = self._json(self._client.get("/v1/jobs/prose"), "Prose")
        return payload if isinstance(payload, list) else []

    def similar_questions(
        self, question: str, *, prose_only: bool = False,
    ) -> list[dict[str, Any]]:
        payload = self._json(
            self._client.post(
                "/v1/jobs/questions/similar",
                json={"question": question, "prose_only": prose_only},
            ), "Similar questions",
        )
        return payload if isinstance(payload, list) else []

    def create_prose(self, *, question: str, text: str, approved: bool = True) -> dict[str, Any]:
        payload = self._json(
            self._client.post(
                "/v1/jobs/prose", json={"question": question, "text": text, "approved": approved}
            ),
            "Prose",
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Prose response was invalid")
        return payload

    def update_prose(
        self, prose_id: str, *, text: str | None = None, approved: bool | None = None
    ) -> dict[str, Any]:
        body = {
            key: value
            for key, value in {"text": text, "approved": approved}.items()
            if value is not None
        }
        payload = self._json(self._client.patch(f"/v1/jobs/prose/{prose_id}", json=body), "Prose")
        if not isinstance(payload, dict):
            raise RuntimeError("Prose response was invalid")
        return payload

    def delete_prose(self, prose_id: str) -> None:
        response = self._client.delete(f"/v1/jobs/prose/{prose_id}")
        if response.status_code == 404:
            raise ReferenceError("Prose not found")
        response.raise_for_status()

    def archive_answers(self) -> list[dict[str, Any]]:
        payload = self._json(self._client.get("/v1/jobs/archive/answers"), "Archive")
        return payload if isinstance(payload, list) else []

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

    def validation_target(self, issue_id: str) -> dict[str, Any]:
        """An unsubmitted application that showed a failed question, another one first."""
        payload = self._json(
            self._client.get(f"/v1/jobs/autofill/failures/{issue_id}/target"), "Target"
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Validation target response was invalid")
        return payload

    def doctor(self) -> None:
        response = self._client.get("/v1/jobs/worker/doctor")
        response.raise_for_status()

    def put_device(self, device_id: str, heartbeat: dict[str, Any]) -> dict[str, Any]:
        """List this device as up; the hosted row is keyed by the device's own id."""
        response = self._client.put(f"/v1/jobs/devices/{device_id}", json=heartbeat)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Device response was invalid")
        return payload
