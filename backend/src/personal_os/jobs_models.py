from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

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
    source_category: Literal["faang", "quant", "other"]
    normalized_locations: list[str]
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
    location_options: list[str] = Field(default_factory=list)


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


class AutofillQueueResponse(BaseModel):
    task_id: UUID
    status: Literal["queued"] = "queued"


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
    preparation_status: str | None = None
    preparation_error: str | None = None
    autofill_status: str | None = None
    autofill_error: str | None = None
    unanswered_required_questions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ApplicationUpdate(BaseModel):
    status: (
        Literal[
            "preparing",
            "ready",
            "submitted",
            "interviewing",
            "offer",
            "rejected",
            "withdrawn",
        ]
        | None
    ) = None
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


class ResumeContact(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=200)
    linkedin: str = Field(default="", max_length=300)
    citizenship: str = Field(default="", max_length=200)


class ResumeEducation(BaseModel):
    school: str = Field(min_length=1, max_length=300)
    degree: str = Field(default="", max_length=300)
    graduation: str = Field(default="", max_length=100)
    gpa: str = Field(default="", max_length=20)


class ResumeEntry(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    org: str = Field(default="", max_length=300)
    date: str = Field(default="", max_length=100)
    bullets: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("bullets")
    @classmethod
    def reject_blank_bullets(cls, value: list[str]) -> list[str]:
        if any(not bullet.strip() for bullet in value):
            raise ValueError("Resume bullets cannot be blank")
        if any(len(bullet) > 1000 for bullet in value):
            raise ValueError("Resume bullets cannot exceed 1000 characters")
        return value


class ResumeSkill(BaseModel):
    category: str = Field(min_length=1, max_length=200)
    items: str = Field(min_length=1, max_length=1000)


class MasterResumeContent(BaseModel):
    """Mirrors what the worker's LaTeX template reads when compiling the PDF."""

    model_config = ConfigDict(extra="allow")

    contact: ResumeContact
    education: ResumeEducation
    experience: list[ResumeEntry] = Field(default_factory=list, max_length=20)
    projects: list[ResumeEntry] = Field(default_factory=list, max_length=20)
    skills: list[ResumeSkill] = Field(default_factory=list, max_length=20)


class ResumePreviewResponse(BaseModel):
    task_id: UUID


class JobDocumentReview(BaseModel):
    document: JobDocument
    source_document: JobDocument
    resume_patch: list[dict[str, Any]] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    changed_word_ratio: Decimal | None = None


class AnswerLibraryItem(BaseModel):
    id: UUID
    question: str
    answer_text: str
    approved: bool
    updated_at: datetime


class AnswerLibraryCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer_text: str = Field(min_length=1, max_length=10000)
    approved: bool

    @field_validator("question", "answer_text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class AnswerLibraryUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    answer_text: str | None = Field(default=None, min_length=1, max_length=10000)
    approved: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            nullable_fields = {
                key
                for key in ("question", "answer_text", "approved")
                if key in value and value[key] is None
            }
            if nullable_fields:
                raise ValueError(
                    f"must not be null: {', '.join(sorted(nullable_fields))}"
                )
        return value

    @field_validator("question", "answer_text")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "AnswerLibraryUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


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
