"""The interactive part of `os hunt apply`, without any terminal prompts.

The terminal only reports. A field fills from a fact, through an active
pattern: one you accepted, or one a model verdict taught and the worker checked
(design §8, R67). The model never writes a value; prose and the cover letter's
slots are the only text it drafts.
The archive is never consulted while applying. Every textarea is seeded with
an answer composed from the narrative library or a Claude draft, and drafts are
listed for review. Everything
still empty — required or optional — is outlined on the page and listed with why
it is empty; the user types it, the worker polls the page, saves every answer it
can see, and watches for a visible Submit control. The user owns Next and
Submit; the run ends when the page reports submission or when the user
interrupts, and both paths save the collected answers.
"""


from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    ARTIFACT_BUILD_ERRORS,
    ASK_LIMIT,
    CODE_CAUSES,
    PERSISTED_CAUSES,
    QUESTION_WIDTH,
    REPAIR_COST,
    REVEAL_PASSES,
    TAG_STYLES,
    TAG_WIDTH,
    TRIED,
    VALUE_WIDTH,
    AnswerChecker,
    Composer,
    Drafter,
    LetterDrafter,
    LetterRenderer,
    SessionContext,
    SessionState,
    ShortDrafter,
    SkippedByUser,
    SubmittedByUser,
    _claude_questions,
    _question_for,
    content_type_for,
    derived_refusal,
    echo,
    error_note,
    leave_letter_for_you,
    member_control,
    page_signature,
    preview,
)
from personal_os_worker.jobs.session.driver import AutofillSession

__all__ = [
    "ARTIFACT_BUILD_ERRORS",
    "ASK_LIMIT",
    "CODE_CAUSES",
    "PERSISTED_CAUSES",
    "QUESTION_WIDTH",
    "REPAIR_COST",
    "REVEAL_PASSES",
    "TAG_STYLES",
    "TAG_WIDTH",
    "TRIED",
    "VALUE_WIDTH",
    "AnswerChecker",
    "AutofillSession",
    "Composer",
    "Drafter",
    "LetterDrafter",
    "LetterRenderer",
    "SessionBase",
    "SessionContext",
    "SessionState",
    "ShortDrafter",
    "SkippedByUser",
    "SubmittedByUser",
    "_claude_questions",
    "_question_for",
    "content_type_for",
    "derived_refusal",
    "echo",
    "error_note",
    "leave_letter_for_you",
    "member_control",
    "page_signature",
    "preview",
]
