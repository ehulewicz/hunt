from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from personal_os_backend.config import Settings
from personal_os_backend.jobs_career_pdf import PdfError, get_pdf_compiler
from personal_os_backend.jobs_storage import get_document_storage
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class FakeStorage:
    def __init__(self, pdf: bytes | None = b"%PDF-1.7-fake") -> None:
        self.pdf = pdf
        self.downloaded: list[UUID] = []

    async def upload_resume(self, user_id: UUID, content: bytes) -> None:
        self.pdf = content

    async def download_resume(self, user_id: UUID) -> bytes | None:
        self.downloaded.append(user_id)
        return self.pdf


class FakeCompiler:
    def __init__(self, error: PdfError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[dict, str, str, str]] = []

    def printed_document(self, document: dict) -> dict:
        return document

    def build_resume_pdf(self, printed: dict) -> bytes:
        return b"%PDF-resume"

    def build_letter_pdf(
        self, contact: dict, text: str, *, date: str, company: str
    ) -> bytes:
        if self.error is not None:
            raise self.error
        self.calls.append((contact, text, date, company))
        return b"%PDF-letter"


class FakePoolConnection:
    """The fact rows the contact header is built from."""

    def __init__(self) -> None:
        self._many: list[dict] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakePoolConnection"]:
        yield self

    async def execute(self, query: str, params: object = None) -> "FakePoolConnection":
        self._many = [
            {"key": "identity.legal_name", "value": "Alex Example"},
            {"key": "contact.email", "value": "alex.example@example.com"},
        ]
        return self

    async def fetchall(self) -> list[dict]:
        return self._many


class FakePool:
    def __init__(self) -> None:
        self.facts = FakePoolConnection()

    @asynccontextmanager
    async def connection(self, **kwargs: object) -> AsyncIterator[FakePoolConnection]:
        yield self.facts


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def compiler() -> FakeCompiler:
    return FakeCompiler()


@pytest.fixture
def client(storage: FakeStorage, compiler: FakeCompiler) -> httpx.AsyncClient:
    app = create_app(
        Settings(
            database_url="postgresql://postgres:password@localhost:5432/postgres",
            supabase_url="https://exampleprojectref.supabase.co",
            allowed_user_id=USER_ID,
            allowed_email="eahtree@gmail.com",
        ),
        lifespan=no_lifespan,
    )
    app.state.jwt_validator = FakeValidator()
    app.state.pool = FakePool()

    async def override_storage() -> FakeStorage:
        return storage

    async def override_compiler() -> FakeCompiler:
        return compiler

    app.dependency_overrides[get_document_storage] = override_storage
    app.dependency_overrides[get_pdf_compiler] = override_compiler
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_the_public_resume_needs_no_auth(
    client: httpx.AsyncClient, storage: FakeStorage
) -> None:
    async with client:
        response = await client.get("/v1/public/resume.pdf")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.7-fake"
        assert response.headers["content-type"] == "application/pdf"
        assert (
            response.headers["content-disposition"]
            == 'inline; filename="Alex_Example.pdf"'
        )
        assert response.headers["cache-control"] == "public, max-age=60"
        assert storage.downloaded == [USER_ID]


async def test_the_public_resume_404s_until_one_is_stored(
    client: httpx.AsyncClient, storage: FakeStorage
) -> None:
    storage.pdf = None
    async with client:
        assert (await client.get("/v1/public/resume.pdf")).status_code == 404


async def test_the_cover_letter_pdf_builds_from_the_contact_facts(
    client: httpx.AsyncClient, compiler: FakeCompiler
) -> None:
    async with client:
        response = await client.post(
            "/v1/jobs/cover-letter/pdf",
            headers=HEADERS,
            json={"text": "Dear team,\n\nA letter.", "company": "Acme",
                  "date": "October 30, 2026"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content == b"%PDF-letter"
        contact, text, date, company = compiler.calls[0]
        assert contact["name"] == "Alex Example"
        assert text == "Dear team,\n\nA letter." and company == "Acme"
        assert date == "October 30, 2026"


async def test_a_letter_that_cannot_build_is_refused(
    client: httpx.AsyncClient, compiler: FakeCompiler
) -> None:
    compiler.error = PdfError("cover_letter must stay within 1 page(s); generated 2")
    async with client:
        refused = await client.post(
            "/v1/jobs/cover-letter/pdf",
            headers=HEADERS,
            json={"text": "Long", "company": "", "date": ""},
        )
        assert refused.status_code == 422
        assert "1 page" in refused.json()["detail"]


def _storage_with(handler) -> "JobsDocumentStorage":  # noqa: F821
    from pydantic import SecretStr

    from personal_os_backend.jobs_storage import JobsDocumentStorage

    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
        supabase_secret_key=SecretStr("test"),
    )
    return JobsDocumentStorage(settings, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(400, json={"statusCode": "404", "error": "not_found"}),
        httpx.Response(400, json={"statusCode": "400", "error": "Object not found"}),
    ],
)
async def test_a_missing_resume_download_is_none(response: httpx.Response) -> None:
    storage = _storage_with(lambda request: response)
    assert await storage.download_resume(USER_ID) is None


async def test_other_storage_errors_still_raise() -> None:
    storage = _storage_with(
        lambda request: httpx.Response(400, json={"statusCode": "400", "error": "bad"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await storage.download_resume(USER_ID)
