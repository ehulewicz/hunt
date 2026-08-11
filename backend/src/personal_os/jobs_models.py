from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

CareerStage = Literal["internship", "new_grad"]
RoleFamily = Literal[
    "software_engineering",
    "data_engineering",
    "ml_engineering",
    "data_science",
    "other",
]


def internship_default() -> list[CareerStage]:
    return ["internship"]


class JobsProfile(BaseModel):
    legal_name: str | None = None
    preferred_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    work_authorization_status: str | None = None
    visa_status: str | None = None
    sponsorship_required: bool | None = None
    school_name: str | None = None
    degree_type: str | None = None
    field_of_study: str | None = None
    graduation_date: date | None = None
    gpa: Decimal | None = Field(default=None, ge=0, le=5)
    earliest_start_date: date | None = None
    earliest_start_text: str | None = None
    years_professional_experience: Decimal | None = Field(default=None, ge=0)
    remote_preference: str | None = None
    willing_to_relocate: bool | None = None
    willing_to_travel: bool | None = None
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    target_career_stages: list[CareerStage] = Field(default_factory=internship_default)
    minimum_salary: Decimal | None = Field(default=None, ge=0)
    professional_summary: str | None = None
    eeo_gender_identity: str | None = None
    eeo_race_ethnicities: list[str] = Field(default_factory=list)
    eeo_veteran_status: str | None = None
    eeo_disability_status: str | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class JobsProfileUpdate(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=80)
    address_line_1: str | None = Field(default=None, max_length=300)
    address_line_2: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=120)
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None
    work_authorization_status: str | None = Field(default=None, max_length=200)
    visa_status: str | None = Field(default=None, max_length=200)
    sponsorship_required: bool | None = None
    school_name: str | None = Field(default=None, max_length=250)
    degree_type: str | None = Field(default=None, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    graduation_date: date | None = None
    gpa: Decimal | None = Field(default=None, ge=0, le=5)
    earliest_start_date: date | None = None
    earliest_start_text: str | None = Field(default=None, max_length=300)
    years_professional_experience: Decimal | None = Field(default=None, ge=0)
    remote_preference: str | None = Field(default=None, max_length=200)
    willing_to_relocate: bool | None = None
    willing_to_travel: bool | None = None
    target_roles: list[str] | None = Field(default=None, max_length=24)
    target_locations: list[str] | None = Field(default=None, max_length=50)
    target_career_stages: list[CareerStage] | None = None
    minimum_salary: Decimal | None = Field(default=None, ge=0)
    professional_summary: str | None = Field(default=None, max_length=4000)
    eeo_gender_identity: str | None = Field(default=None, max_length=120)
    eeo_race_ethnicities: list[str] | None = Field(default=None, max_length=20)
    eeo_veteran_status: str | None = Field(default=None, max_length=120)
    eeo_disability_status: str | None = Field(default=None, max_length=120)
    completed: bool | None = None

    @field_validator("target_roles", "target_locations", "eeo_race_ethnicities")
    @classmethod
    def normalize_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class JobListing(BaseModel):
    id: UUID
    company_name: str
    title: str
    location: str | None
    salary_text: str | None
    application_url: str | None
    discovery_url: str | None
    role_families: list[RoleFamily]
    career_stage: CareerStage | None
    feed_region: Literal["usa", "international"] | None
    source_posted_at: date | None
    source_age_days: int | None
    status: str
    application_id: UUID | None = None


class JobListingPage(BaseModel):
    items: list[JobListing]
    total: int
    limit: int
    offset: int


class ListingSyncResponse(BaseModel):
    source: str
    seen: int
    open: int
    synced_at: datetime


class BatchPrepareRequest(BaseModel):
    posting_ids: list[UUID] = Field(min_length=1, max_length=100)


class BatchPrepareResponse(BaseModel):
    application_ids: list[UUID]
    queued_task_ids: list[UUID]


class JobApplication(BaseModel):
    id: UUID
    posting_id: UUID
    company_name: str
    title: str
    location: str | None
    application_url: str | None
    status: str
    applied_at: datetime | None
    next_action: str | None
    next_action_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationUpdate(BaseModel):
    status: Literal[
        "preparing",
        "ready",
        "submitted",
        "interviewing",
        "offer",
        "rejected",
        "withdrawn",
        "archived",
    ] | None = None
    next_action: str | None = Field(default=None, max_length=1000)
    next_action_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=10000)


class JobDocument(BaseModel):
    id: UUID
    application_id: UUID | None
    document_type: str
    title: str
    family_id: UUID
    version: int
    source_document_id: UUID | None
    content_text: str | None
    storage_path: str | None
    mime_type: str | None
    status: str
    is_current: bool
    created_at: datetime
    updated_at: datetime


class AnswerLibraryItem(BaseModel):
    id: UUID
    key: str
    normalized_question: str | None
    answer_text: str
    answer_type: str
    approved: bool
    updated_at: datetime


class AnswerLibraryUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=160)
    normalized_question: str | None = Field(default=None, max_length=1000)
    answer_text: str = Field(min_length=1, max_length=10000)
    answer_type: str = Field(default="text", max_length=40)
    approved: bool = False


class WorkerCredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WorkerCredential(BaseModel):
    id: UUID
    name: str
    scopes: list[str]
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class WorkerCredentialIssued(WorkerCredential):
    token: str


class WorkerTask(BaseModel):
    id: UUID
    task_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_expires_at: datetime | None


class WorkerClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)


class WorkerClaimResponse(BaseModel):
    task: WorkerTask | None


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)


class WorkerTaskResult(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    result: dict[str, Any] = Field(default_factory=dict)


class WorkerTaskFailure(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    error_message: str = Field(min_length=1, max_length=4000)
    retry: bool = True


class WorkerApplicationBundle(BaseModel):
    application_id: UUID
    application_url: str
    company_name: str
    title: str
    description_text: str | None
    profile: dict[str, Any]
    main_resume: dict[str, Any]
    approved_answers: list[dict[str, Any]]
