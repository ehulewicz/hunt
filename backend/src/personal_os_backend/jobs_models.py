from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from personal_os_backend.jobs_registry import FillEventCreate

CareerStage = Literal["internship", "new_grad"]
# The everyday view and the board's numbering start with these; the worker
# mirrors the list for its --family choices.
DEFAULT_TECHNICAL_FAMILIES = (
    "software_engineering",
    "data_engineering",
    "ml_engineering",
)

RoleFamily = Literal[
    "software_engineering",
    "data_engineering",
    "ml_engineering",
    "data_science",
    "other",
]
# A label for querying runs and fill events, never behavior.
AutofillAts = Literal["greenhouse", "ashby", "lever", "workday", "generic"]
FailureStatus = Literal[
    "open", "diagnosed", "patch_ready", "replay_passed", "live_verified", "fixed", "archived",
    "repaired", "validated",
]


def internship_default() -> list[CareerStage]:
    return ["internship"]


class JobListing(BaseModel):
    id: UUID
    created_at: datetime
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
    application_status: str | None = None
    row: int | None = None  # canonical number on the board; None once applied to


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
    unanswered_required_questions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


APPLICATION_STATUSES = (
    "applying",
    "applied",
    "oa",
    "oa_completed",
    "interview",
    "offer",
    "rejected",
    "skipped",
)
ApplicationStatus = Literal[
    "applying", "applied", "oa", "oa_completed", "interview", "offer", "rejected", "skipped"
]


class ApplicationCreate(BaseModel):
    posting_id: UUID
    status: Literal["applying", "applied"] = "applying"


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    next_action: str | None = Field(default=None, max_length=1000)
    next_action_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=10000)


class AutofillRunCreate(BaseModel):
    posting_id: UUID
    ats: AutofillAts
    adapter_version: str = Field(min_length=1, max_length=200)


class AutofillRunContext(BaseModel):
    run_id: UUID
    application_id: UUID
    ats: AutofillAts
    application_url: str
    discovery_url: str | None
    company_name: str
    title: str
    description_text: str | None
    facts: list[dict[str, Any]]
    # Only active patterns; candidates wait in the Inbox (design R43).
    patterns: list[dict[str, Any]]
    # Approved prose, for long-form questions.
    prose: list[dict[str, Any]]
    # Strategies a validated repair found for a particular control (design §10.3).
    control_preferences: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_company_applications: list[dict[str, Any]]


class AutofillAnswer(BaseModel):
    """Validated history value; recording it never approves future reuse."""
    question: str = Field(min_length=1, max_length=1000)
    answer_text: str = Field(max_length=10000)
    source: str = Field(default="manual", max_length=40)
    step_number: int | None = Field(default=None, ge=0)
    page_url: str | None = Field(default=None, min_length=1, max_length=4000)
    control_kind: str | None = Field(default=None, max_length=100)
    field_fingerprint: str | None = Field(default=None, max_length=500)
    resolver_id: str | None = Field(default=None, max_length=200)
    proposed_value: str | None = Field(default=None, max_length=10000)
    # Whether the question wants prose; a submitted long-form answer is saved as prose.
    form: Literal["short", "long"] | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class AutofillObservation(AutofillAnswer):
    event_index: int = Field(ge=0)
    step_number: int = Field(ge=0)
    page_url: str = Field(min_length=1, max_length=4000)


class AutofillStepCreate(BaseModel):
    step_number: int = Field(ge=0)
    url: str = Field(min_length=1, max_length=4000)
    filled_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    failures: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    observations: list[AutofillObservation] = Field(default_factory=list, max_length=500)
    fill_events: list[FillEventCreate] = Field(default_factory=list, max_length=500)


class AutofillRunComplete(BaseModel):
    final_url: str = Field(min_length=1, max_length=4000)
    submitted: bool = False
    answers: list[AutofillAnswer] = Field(default_factory=list, max_length=200)


class AutofillRunFinish(AutofillRunComplete):
    application_status: Literal["applied", "skipped", "archived"]

    @model_validator(mode="after")
    def consistent_outcome(self) -> "AutofillRunFinish":
        if self.submitted != (self.application_status == "applied"):
            raise ValueError("submitted must agree with application_status")
        return self


class AutofillRunStop(BaseModel):
    status: Literal["abandoned", "failed"]


class AutofillFailureIssue(BaseModel):
    id: UUID
    signature: str
    ats: str
    category: str
    field_label: str | None
    control_kind: str | None
    status: FailureStatus
    diagnosis: str | None
    proposed_patch: str | None
    fixed_commit: str | None
    verification_notes: str | None
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    stopped_at: Literal["format", "strategy"] | None = None
    candidate: dict[str, Any] | None = None
    repaired_at: datetime | None = None
    validated_at: datetime | None = None


class AutofillFailureOccurrence(BaseModel):
    id: UUID
    run_id: UUID
    step_number: int
    field_fingerprint: str | None
    intended_value: str | None
    observed_value: str | None
    error_message: str | None
    dom_signals: dict[str, Any]
    artifact_paths: dict[str, str]
    adapter_version: str
    created_at: datetime
    application_url: str | None = None


class AutofillFailureDetail(AutofillFailureIssue):
    occurrences: list[AutofillFailureOccurrence] = Field(default_factory=list)


class AutofillFailureUpdate(BaseModel):
    status: FailureStatus | None = None
    # The repaired rule: {"kind": "strategy" | "format", ...} (design §10.3).
    candidate: dict[str, Any] | None = None
    diagnosis: str | None = Field(default=None, max_length=30000)
    proposed_patch: str | None = Field(default=None, max_length=200000)
    fixed_commit: str | None = Field(default=None, max_length=100)
    verification_notes: str | None = Field(default=None, max_length=30000)


class AutofillArtifactResponse(BaseModel):
    storage_path: str


class ValidationTarget(BaseModel):
    """An unsubmitted application showing the failed question, a fresh one first (R26)."""

    application_url: str
    company_name: str
    title: str
    original: bool
