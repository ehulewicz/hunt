from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from personal_os.auth import CurrentPrincipal
from personal_os.jobs_models import (
    AnswerLibraryCreate,
    AnswerLibraryItem,
    AnswerLibraryUpdate,
    ApplicationUpdate,
    AutofillArtifactResponse,
    AutofillFailureDetail,
    AutofillFailureIssue,
    AutofillFailureUpdate,
    AutofillRunComplete,
    AutofillRunContext,
    AutofillRunCreate,
    AutofillRunStop,
    AutofillStepCreate,
    JobApplication,
    JobDocument,
    JobListingPage,
    JobsProfile,
    JobsProfileUpdate,
    ListingSyncResponse,
    MasterResumeContent,
    QueueApplicationsRequest,
    RoleFamily,
)
from personal_os.jobs_sources import SpeedyApplySource
from personal_os.jobs_storage import JobsDocumentStorage
from personal_os.jobs_store import JobsStore, get_jobs_store
from personal_os.jobs_sync import run_sync

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
    role_families: Annotated[list[RoleFamily] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListingPage:
    normalized_locations = list(
        dict.fromkeys(location.strip() for location in locations or [] if location.strip())
    )
    normalized_role_families = list(dict.fromkeys(role_families or []))
    if len(normalized_locations) > 50 or any(
        len(location) > 200 for location in normalized_locations
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid location filters")
    return await store.list_listings(
        principal.user_id,
        category=category,
        locations=normalized_locations,
        role_families=normalized_role_families,
        limit=limit,
        offset=offset,
    )


@router.post("/listings/sync", response_model=ListingSyncResponse)
async def sync_listings(principal: CurrentPrincipal, store: Store) -> ListingSyncResponse:
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            return await run_sync(principal.user_id, SpeedyApplySource(client), store)
    except httpx.HTTPError as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Listing source could not be fetched"
        ) from error
    except RuntimeError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error


@router.get("/applications", response_model=list[JobApplication])
async def list_applications(principal: CurrentPrincipal, store: Store) -> list[JobApplication]:
    return await store.list_applications(principal.user_id)


@router.post("/applications/queue", response_model=list[JobApplication])
async def queue_applications(
    request: QueueApplicationsRequest, principal: CurrentPrincipal, store: Store
) -> list[JobApplication]:
    queued = set(await store.queue_applications(principal.user_id, request.posting_ids))
    if not queued:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No eligible listings were selected")
    applications = await store.list_applications(principal.user_id)
    return [item for item in applications if item.id in queued]


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


@router.put("/documents/{document_id}/master-resume", response_model=JobDocument)
async def update_master_resume_draft(
    document_id: UUID,
    content: MasterResumeContent,
    principal: CurrentPrincipal,
    store: Store,
) -> JobDocument:
    document = await store.update_master_resume_draft(principal.user_id, document_id, content)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft main resume not found")
    return document


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
    "/autofill/runs",
    response_model=AutofillRunContext,
    status_code=status.HTTP_201_CREATED,
)
async def create_autofill_run(
    item: AutofillRunCreate, principal: CurrentPrincipal, store: Store
) -> AutofillRunContext:
    run = await store.create_autofill_run(principal.user_id, item)
    if run is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Listing is closed, has no direct application URL, or profile is missing",
        )
    return run


@router.post("/autofill/runs/{run_id}/steps", response_model=list[AutofillFailureIssue])
async def persist_autofill_step(
    run_id: UUID,
    item: AutofillStepCreate,
    principal: CurrentPrincipal,
    store: Store,
) -> list[AutofillFailureIssue]:
    issues = await store.persist_autofill_step(principal.user_id, run_id, item)
    if issues is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Running autofill session not found")
    return issues


@router.post("/autofill/runs/{run_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_autofill_run(
    run_id: UUID,
    item: AutofillRunComplete,
    principal: CurrentPrincipal,
    store: Store,
) -> Response:
    if not await store.complete_autofill_run(principal.user_id, run_id, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Running autofill session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/autofill/runs/{run_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_autofill_run(
    run_id: UUID,
    item: AutofillRunStop,
    principal: CurrentPrincipal,
    store: Store,
) -> Response:
    if not await store.stop_autofill_run(principal.user_id, run_id, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Running autofill session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/autofill/runs/{run_id}/artifacts", response_model=AutofillArtifactResponse)
async def upload_autofill_artifact(
    run_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    store: Store,
    artifact: Annotated[UploadFile, File()],
) -> AutofillArtifactResponse:
    if not await store.owns_autofill_run(principal.user_id, run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Autofill session not found")
    allowed = {"image/png", "text/html", "text/plain", "application/json"}
    if artifact.content_type not in allowed:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported artifact type")
    content = await artifact.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Artifact is too large")
    suffix = {
        "image/png": "png",
        "text/html": "html",
        "text/plain": "txt",
        "application/json": "json",
    }[artifact.content_type]
    path = f"{principal.user_id}/{run_id}/{uuid4()}.{suffix}"
    try:
        await JobsDocumentStorage(request.app.state.settings).upload_autofill_artifact(
            path, content, artifact.content_type
        )
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Autofill storage unavailable"
        ) from None
    except (ValueError, httpx.HTTPError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Artifact upload failed") from None
    return AutofillArtifactResponse(storage_path=path)


@router.get("/autofill/failures", response_model=list[AutofillFailureIssue])
async def list_autofill_failures(
    principal: CurrentPrincipal, store: Store
) -> list[AutofillFailureIssue]:
    return await store.list_autofill_failures(principal.user_id)


@router.get("/autofill/failures/{issue_id}", response_model=AutofillFailureDetail)
async def get_autofill_failure(
    issue_id: UUID, principal: CurrentPrincipal, store: Store
) -> AutofillFailureDetail:
    issue = await store.get_autofill_failure(principal.user_id, issue_id)
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Autofill failure not found")
    return issue


@router.get("/autofill/failures/{issue_id}/artifact")
async def download_autofill_failure_artifact(
    issue_id: UUID,
    path: Annotated[str, Query(min_length=1, max_length=1000)],
    request: Request,
    principal: CurrentPrincipal,
    store: Store,
) -> Response:
    owned = await store.autofill_artifact_belongs_to_issue(principal.user_id, issue_id, path)
    if not path.startswith(f"{principal.user_id}/") or not owned:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Autofill artifact not found")
    try:
        storage = JobsDocumentStorage(request.app.state.settings)
        content = await storage.download_autofill_artifact(path)
    except RuntimeError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Autofill storage unavailable"
        ) from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Artifact download failed") from None
    media = {
        ".png": "image/png",
        ".html": "text/html",
        ".txt": "text/plain",
        ".json": "application/json",
    }
    content_type = next(
        (value for key, value in media.items() if path.endswith(key)),
        "application/octet-stream",
    )
    return Response(content, media_type=content_type)


@router.patch("/autofill/failures/{issue_id}", response_model=AutofillFailureIssue)
async def update_autofill_failure(
    issue_id: UUID,
    item: AutofillFailureUpdate,
    principal: CurrentPrincipal,
    store: Store,
) -> AutofillFailureIssue:
    try:
        issue = await store.update_autofill_failure(principal.user_id, issue_id, item)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if issue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Autofill failure not found")
    return issue


@router.get("/worker/doctor")
async def worker_doctor(principal: CurrentPrincipal) -> dict[str, str]:
    return {"status": "ok", "user_id": str(principal.user_id)}


