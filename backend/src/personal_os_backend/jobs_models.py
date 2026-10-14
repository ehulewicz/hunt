import re
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
AnswerReusePolicy = Literal["exact", "contextual", "pattern"]
AutofillAts = Literal["greenhouse", "ashby", "lever", "generic"]
FailureStatus = Literal[
    "open", "diagnosed", "patch_ready", "replay_passed", "live_verified", "fixed"
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


def validate_pattern(reuse_policy: str | None, question: str | None) -> None:
    """A pattern row's question is a regular expression; refuse ones that cannot compile."""
    if reuse_policy != "pattern" or question is None:
        return
    try:
        re.compile(question, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"question must be a valid regular expression: {error}") from None


class AnswerReviewItem(BaseModel):
    id: UUID
    application_id: UUID
    run_id: UUID
    event_index: int
    step_number: int
    page_url: str
    question: str
    answer_text: str
    answer_source: str
    control_kind: str | None
    field_fingerprint: str | None
    resolver_id: str | None
    proposed_value: str | None
    reason: Literal["changed_proposal", "model_draft", "manual_answer"]
    completion_value: str | None
    completion_status: str | None
    created_at: datetime


class AnswerLibraryItem(BaseModel):
    id: UUID
    question: str
    normalized_key: str
    answer_text: str
    reuse_policy: AnswerReusePolicy
    approved: bool
    updated_at: datetime


class AnswerLibraryCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer_text: str = Field(min_length=1, max_length=10000)
    reuse_policy: AnswerReusePolicy = "exact"
    approved: bool

    @field_validator("question", "answer_text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def pattern_must_compile(self) -> "AnswerLibraryCreate":
        validate_pattern(self.reuse_policy, self.question)
        return self


class AnswerLibraryUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    answer_text: str | None = Field(default=None, min_length=1, max_length=10000)
    reuse_policy: AnswerReusePolicy | None = None
    approved: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            nullable_fields = {
                key
                for key in ("question", "answer_text", "reuse_policy", "approved")
                if key in value and value[key] is None
            }
            if nullable_fields:
                raise ValueError(f"must not be null: {', '.join(sorted(nullable_fields))}")
        return value

    @model_validator(mode="after")
    def pattern_must_compile(self) -> "AnswerLibraryUpdate":
        validate_pattern(self.reuse_policy, self.question)
        return self

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
    approved_answers: list[dict[str, Any]]
    scoped_answers: list[dict[str, Any]]
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


class AutofillFailureDetail(AutofillFailureIssue):
    occurrences: list[AutofillFailureOccurrence] = Field(default_factory=list)


class AutofillFailureUpdate(BaseModel):
    status: FailureStatus | None = None
    diagnosis: str | None = Field(default=None, max_length=30000)
    proposed_patch: str | None = Field(default=None, max_length=200000)
    fixed_commit: str | None = Field(default=None, max_length=100)
    verification_notes: str | None = Field(default=None, max_length=30000)


class AutofillArtifactResponse(BaseModel):
    storage_path: str
