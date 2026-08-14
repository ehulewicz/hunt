from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from personal_os.auth import CurrentPrincipal
from personal_os.config import Settings
from personal_os.jobs_models import (
    AnswerLibraryCreate,
    AnswerLibraryItem,
    AnswerLibraryUpdate,
    ApplicationUpdate,
    AutofillQueueResponse,
    BatchPrepareRequest,
    BatchPrepareResponse,
    JobApplication,
    JobDocument,
    JobDocumentReview,
    JobListingPage,
    JobsProfile,
    JobsProfileUpdate,
    MasterResumeContent,
    ResumePreviewResponse,
    WorkerApplicationBundle,
    WorkerClaimRequest,
    WorkerClaimResponse,
    WorkerCredential,
    WorkerCredentialCreate,
    WorkerCredentialIssued,
    WorkerHeartbeatRequest,
    WorkerTaskFailure,
    WorkerTaskResult,
)
from personal_os.jobs_storage import JobsDocumentStorage
from personal_os.jobs_store import JobsStore, get_jobs_store
from personal_os.worker_auth import CurrentWorker

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])
Store = Annotated[JobsStore, Depends(get_jobs_store)]


@router.get("/profile", response_model=JobsProfile)
async def get_profile(principal: CurrentPrincipal, store: Store) -> JobsProfile:
    try:
        return await store.get_profile(principal.user_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jobs profile not found") from None


@router.patch("/profile", response_model=JobsProfile)
async def update_profile(
    update: JobsProfileUpdate, principal: CurrentPrincipal, store: Store
) -> JobsProfile:
    return await store.update_profile(principal.user_id, update)


@router.get("/listings", response_model=JobListingPage)
async def list_listings(
    principal: CurrentPrincipal,
    store: Store,
    category: Annotated[Literal["faang", "quant"] | None, Query()] = None,
    locations: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListingPage:
    normalized_locations = list(
        dict.fromkeys(location.strip() for location in locations or [] if location.strip())
    )
    if len(normalized_locations) > 50 or any(
        len(location) > 200 for location in normalized_locations
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid location filters")
    return await store.list_listings(
        principal.user_id,
        category=category,
        locations=normalized_locations,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/applications/batch-prepare",
    response_model=BatchPrepareResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def batch_prepare(
    request: BatchPrepareRequest, principal: CurrentPrincipal, store: Store
) -> BatchPrepareResponse:
    response = await store.prepare_applications(principal.user_id, request.posting_ids)
    if not response.application_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No eligible listings were selected")
    return response


@router.get("/applications", response_model=list[JobApplication])
async def list_applications(principal: CurrentPrincipal, store: Store) -> list[JobApplication]:
    return await store.list_applications(principal.user_id)


@router.patch("/applications/{application_id}", response_model=JobApplication)
async def update_application(
    application_id: UUID,
    update: ApplicationUpdate,
    principal: CurrentPrincipal,
    store: Store,
) -> JobApplication:
    application = await store.update_application(principal.user_id, application_id, update)
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


@router.delete("/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_application(
    application_id: UUID, principal: CurrentPrincipal, store: Store
) -> Response:
    if not await store.archive_application(principal.user_id, application_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/applications/{application_id}/autofill",
    response_model=AutofillQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_application_autofill(
    application_id: UUID, principal: CurrentPrincipal, store: Store
) -> AutofillQueueResponse:
    task_id = await store.queue_autofill(principal.user_id, application_id)
    if task_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Application needs an approved tailored resume before autofill",
        )
    return AutofillQueueResponse(task_id=task_id)


@router.get("/documents", response_model=list[JobDocument])
async def list_documents(principal: CurrentPrincipal, store: Store) -> list[JobDocument]:
    return await store.list_documents(principal.user_id)


@router.post(
    "/documents/master-resume",
    response_model=JobDocument,
    status_code=status.HTTP_201_CREATED,
)
async def create_master_resume_draft(
    content: MasterResumeContent, principal: CurrentPrincipal, store: Store
) -> JobDocument:
    document = await store.create_master_resume_draft(principal.user_id, content)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Main resume not found")
    return document


@router.post("/documents/{document_id}/preview", response_model=ResumePreviewResponse)
async def queue_resume_preview(
    document_id: UUID, principal: CurrentPrincipal, store: Store
) -> ResumePreviewResponse:
    task_id = await store.queue_resume_preview(principal.user_id, document_id)
    if task_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft main resume not found")
    return ResumePreviewResponse(task_id=task_id)


@router.post("/documents/{document_id}/approve", response_model=JobDocument)
async def approve_document(
    document_id: UUID, principal: CurrentPrincipal, store: Store
) -> JobDocument:
    document = await store.approve_document(principal.user_id, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft document not found")
    return document


@router.get("/documents/{document_id}/file")
async def download_document(
    document_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    store: Store,
) -> Response:
    stored = await store.get_document_storage_path(principal.user_id, document_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document PDF not found")
    path, title = stored
    try:
        content = await JobsDocumentStorage(request.app.state.settings).download_pdf(path)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage unavailable"
        ) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Document download failed") from None
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_") or "resume"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@router.get("/documents/{document_id}/review", response_model=JobDocumentReview)
async def get_document_review(
    document_id: UUID, principal: CurrentPrincipal, store: Store
) -> JobDocumentReview:
    review = await store.get_document_review(principal.user_id, document_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tailored resume review not found")
    return review


@router.get("/answers", response_model=list[AnswerLibraryItem])
async def list_answers(principal: CurrentPrincipal, store: Store) -> list[AnswerLibraryItem]:
    return await store.list_answers(principal.user_id)


@router.post("/answers", response_model=AnswerLibraryItem, status_code=status.HTTP_201_CREATED)
async def create_answer(
    item: AnswerLibraryCreate,
    principal: CurrentPrincipal,
    store: Store,
) -> AnswerLibraryItem:
    return await store.create_answer(principal.user_id, item)


@router.patch("/answers/{answer_id}", response_model=AnswerLibraryItem)
async def update_answer(
    answer_id: UUID,
    item: AnswerLibraryUpdate,
    principal: CurrentPrincipal,
    store: Store,
) -> AnswerLibraryItem:
    answer = await store.update_answer(principal.user_id, answer_id, item)
    if answer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    return answer


@router.post(
    "/worker-credentials",
    response_model=WorkerCredentialIssued,
    status_code=status.HTTP_201_CREATED,
)
async def create_worker_credential(
    payload: WorkerCredentialCreate,
    request: Request,
    principal: CurrentPrincipal,
    store: Store,
) -> WorkerCredentialIssued:
    settings: Settings = request.app.state.settings
    secret = settings.worker_jwt_secret
    if secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Worker pairing is unavailable")
    credential_id = uuid4()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(days=settings.worker_token_lifetime_days)
    token = jwt.encode(
        {
            "sub": str(principal.user_id),
            "email": principal.email,
            "jti": str(credential_id),
            "scope": "jobs:worker",
            "iss": settings.worker_jwt_issuer,
            "aud": settings.worker_jwt_audience,
            "iat": issued_at,
            "exp": expires_at,
        },
        secret.get_secret_value(),
        algorithm="HS256",
    )
    token_text = token.decode() if isinstance(token, bytes) else token
    credential = await store.create_worker_credential(
        principal.user_id,
        credential_id=credential_id,
        name=payload.name,
        token_hash=hashlib.sha256(token_text.encode()).hexdigest(),
        expires_at=expires_at,
    )
    return WorkerCredentialIssued(**credential.model_dump(), token=token_text)


@router.get("/worker-credentials", response_model=list[WorkerCredential])
async def list_worker_credentials(
    principal: CurrentPrincipal, store: Store
) -> list[WorkerCredential]:
    return await store.list_worker_credentials(principal.user_id)


@router.delete("/worker-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_worker_credential(
    credential_id: UUID, principal: CurrentPrincipal, store: Store
) -> Response:
    if not await store.revoke_worker_credential(principal.user_id, credential_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker credential not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/worker/tasks/claim", response_model=WorkerClaimResponse)
async def claim_worker_task(
    payload: WorkerClaimRequest, worker: CurrentWorker, store: Store
) -> WorkerClaimResponse:
    return WorkerClaimResponse(task=await store.claim_task(worker.user_id, payload.worker_id))


@router.get(
    "/worker/applications/{application_id}/bundle",
    response_model=WorkerApplicationBundle,
)
async def get_worker_application_bundle(
    application_id: UUID, worker: CurrentWorker, store: Store
) -> WorkerApplicationBundle:
    bundle = await store.get_worker_application_bundle(worker.user_id, application_id)
    if bundle is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Application is missing an approved main resume or company URL",
        )
    return bundle


@router.get("/worker/doctor")
async def worker_doctor(worker: CurrentWorker) -> dict[str, str]:
    return {"status": "ok", "user_id": str(worker.user_id)}


@router.get("/worker/applications/{application_id}/resume")
async def download_worker_resume(
    application_id: UUID,
    request: Request,
    worker: CurrentWorker,
    store: Store,
) -> Response:
    stored = await store.get_approved_resume_storage_path(worker.user_id, application_id)
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approved tailored resume not found")
    path, title = stored
    try:
        content = await JobsDocumentStorage(request.app.state.settings).download_pdf(path)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage unavailable"
        ) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Document download failed") from None
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_") or "resume"
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@router.get("/worker/documents/{document_id}")
async def get_worker_document(
    document_id: UUID, worker: CurrentWorker, store: Store
) -> Response:
    content = await store.get_master_resume_draft_content(worker.user_id, document_id)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Main resume draft not found")
    return Response(content, media_type="application/json")


@router.post("/worker/tasks/{task_id}/pdf")
async def upload_worker_pdf(
    task_id: UUID,
    request: Request,
    worker: CurrentWorker,
    store: Store,
    document: Annotated[UploadFile, File()],
    worker_id: Annotated[str, Form(min_length=1, max_length=120)],
) -> dict[str, str]:
    application_id = await store.get_worker_task_application_id(worker.user_id, task_id, worker_id)
    preview_document_id = (
        None
        if application_id is not None
        else await store.get_worker_task_document_id(worker.user_id, task_id, worker_id)
    )
    if application_id is None and preview_document_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Worker task is no longer active")
    content = await document.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF exceeds 5 MiB")
    if document.content_type != "application/pdf" or not content.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are accepted")
    path = (
        f"{worker.user_id}/applications/{application_id}/{task_id}.pdf"
        if application_id is not None
        else f"{worker.user_id}/resumes/{preview_document_id}/{task_id}.pdf"
    )
    try:
        await JobsDocumentStorage(request.app.state.settings).upload_pdf(path, content)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Document storage unavailable"
        ) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Document upload failed") from None
    return {"storage_path": path}


@router.post("/worker/tasks/{task_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat_worker_task(
    task_id: UUID,
    payload: WorkerHeartbeatRequest,
    worker: CurrentWorker,
    store: Store,
) -> Response:
    if not await store.heartbeat_task(worker.user_id, task_id, payload.worker_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Worker task lease is no longer active")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/worker/tasks/{task_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_worker_task(
    task_id: UUID,
    payload: WorkerTaskResult,
    worker: CurrentWorker,
    store: Store,
) -> Response:
    if not await store.complete_task(worker.user_id, task_id, payload.worker_id, payload.result):
        raise HTTPException(status.HTTP_409_CONFLICT, "Worker task is no longer active")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/worker/tasks/{task_id}/fail", status_code=status.HTTP_204_NO_CONTENT)
async def fail_worker_task(
    task_id: UUID,
    payload: WorkerTaskFailure,
    worker: CurrentWorker,
    store: Store,
) -> Response:
    if not await store.fail_task(
        worker.user_id,
        task_id,
        payload.worker_id,
        payload.error_message,
        retry=payload.retry,
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Worker task is no longer active")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
