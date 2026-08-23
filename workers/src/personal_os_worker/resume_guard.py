from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

ALLOWED_PATH = re.compile(r"^/(experience|projects)/(\d+)/bullets/(\d+)$|^/skills/(\d+)/items$")


class UnsafeResumeChange(ValueError):
    """Raised when a proposed resume patch violates the minimal-tailoring contract."""


@dataclass(frozen=True, slots=True)
class ResumeValidation:
    candidate: dict[str, Any]
    changed_word_ratio: float
    changed_paths: tuple[str, ...]
    skipped: tuple[str, ...] = ()


def apply_and_validate(
    base: dict[str, Any],
    patches: list[dict[str, Any]],
    *,
    job_description: str,
    fact_context: str,
    maximum_change_ratio: float = 0.10,
) -> ResumeValidation:
    candidate = copy.deepcopy(base)
    changed_paths: list[str] = []
    skipped: list[str] = []
    for patch in patches:
        if patch.get("op") != "replace":
            raise UnsafeResumeChange("Only replacement patches are allowed")
        path = str(patch.get("path", ""))
        match = ALLOWED_PATH.fullmatch(path)
        replacement = patch.get("value")
        if match is None or not isinstance(replacement, str) or not replacement.strip():
            raise UnsafeResumeChange(f"Patch path is not allowed: {path}")
        parent, key = resolve_path(candidate, path)
        original = parent[key]
        if not isinstance(original, str):
            raise UnsafeResumeChange(f"Patch target is not text: {path}")
        unsupported = unsupported_new_keywords(
            original, replacement, job_description, fact_context
        )
        if unsupported:
            # Drop only the inflated patch; the remaining ones still tailor the resume.
            skipped.append(f"{path}: {', '.join(unsupported[:12])}")
            continue
        parent[key] = replacement.strip()
        changed_paths.append(path)

    ensure_structure_unchanged(base, candidate)
    ratio = changed_word_ratio(base, candidate)
    if ratio > maximum_change_ratio:
        raise UnsafeResumeChange(
            f"Resume patch changes {ratio:.1%} of words; maximum is {maximum_change_ratio:.0%}"
        )
    return ResumeValidation(
        candidate=candidate,
        changed_word_ratio=ratio,
        changed_paths=tuple(changed_paths),
        skipped=tuple(skipped),
    )


def resolve_path(document: dict[str, Any], path: str) -> tuple[Any, str | int]:
    parts = path.lstrip("/").split("/")
    current: Any = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    key: str | int = int(parts[-1]) if isinstance(current, list) else parts[-1]
    return current, key


def ensure_structure_unchanged(base: dict[str, Any], candidate: dict[str, Any]) -> None:
    if base.get("contact") != candidate.get("contact"):
        raise UnsafeResumeChange("Contact details cannot be tailored")
    if base.get("education") != candidate.get("education"):
        raise UnsafeResumeChange("Education cannot be tailored")
    for section in ("experience", "projects"):
        before = base.get(section)
        after = candidate.get(section)
        if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
            raise UnsafeResumeChange(f"{section.title()} structure changed")
        for old_entry, new_entry in zip(before, after, strict=True):
            for key in ("title", "org", "date"):
                if old_entry.get(key) != new_entry.get(key):
                    raise UnsafeResumeChange(f"{section.title()} identity changed")
            if len(old_entry.get("bullets", [])) != len(new_entry.get("bullets", [])):
                raise UnsafeResumeChange(f"{section.title()} bullet count changed")
    before_skills = base.get("skills")
    after_skills = candidate.get("skills")
    if not isinstance(before_skills, list) or not isinstance(after_skills, list):
        raise UnsafeResumeChange("Skills structure is missing")
    if len(before_skills) != len(after_skills):
        raise UnsafeResumeChange("Skills category count changed")
    for old_skill, new_skill in zip(before_skills, after_skills, strict=True):
        if old_skill.get("category") != new_skill.get("category"):
            raise UnsafeResumeChange("Skills categories changed")


def changed_word_ratio(base: dict[str, Any], candidate: dict[str, Any]) -> float:
    before = words(resume_text(base))
    after = words(resume_text(candidate))
    if not before:
        raise UnsafeResumeChange("Main resume has no words")
    changed = 0
    for operation, start_a, end_a, start_b, end_b in SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if operation != "equal":
            changed += max(end_a - start_a, end_b - start_b)
    return changed / len(before)


def resume_text(resume: dict[str, Any]) -> str:
    values: list[str] = []
    for section in ("contact", "education"):
        values.extend(str(value) for value in resume.get(section, {}).values())
    for section in ("experience", "projects"):
        for entry in resume.get(section, []):
            values.extend(str(entry.get(key, "")) for key in ("title", "org", "date"))
            values.extend(str(bullet) for bullet in entry.get("bullets", []))
    for skill in resume.get("skills", []):
        values.extend((str(skill.get("category", "")), str(skill.get("items", ""))))
    return " ".join(values)


def words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", value.casefold())


# Irregular past tenses common in resume bullets, which suffix stripping cannot reach.
IRREGULAR_STEMS = {
    "began": "begin",
    "born": "bear",
    "bought": "buy",
    "brought": "bring",
    "built": "build",
    "chose": "choose",
    "drove": "drive",
    "found": "find",
    "grew": "grow",
    "grown": "grow",
    "held": "hold",
    "kept": "keep",
    "led": "lead",
    "made": "make",
    "met": "meet",
    "ran": "run",
    "sold": "sell",
    "spoke": "speak",
    "taught": "teach",
    "thought": "think",
    "took": "take",
    "won": "win",
    "wrote": "write",
    "written": "write",
}

STRIPPABLE_SUFFIXES = ("ing", "ed", "es", "s", "ly", "al", "ion", "ment")


def stem(word: str) -> str:
    """Reduce a word to a crude stem.

    Linguistic accuracy is not the goal; both sides of every comparison are
    normalized identically, so a consistent stem is what matters.
    """
    stemmed = IRREGULAR_STEMS.get(word, word)
    changed = True
    while changed:
        changed = False
        for suffix in STRIPPABLE_SUFFIXES:
            if stemmed.endswith(suffix) and len(stemmed) - len(suffix) >= 4:
                stemmed = stemmed[: -len(suffix)]
                changed = True
                break
    return stemmed[:-1] + "i" if stemmed.endswith("y") else stemmed


def stems(value: str) -> set[str]:
    return {stem(word) for word in words(value)}


def unsupported_new_keywords(
    original: str, replacement: str, job_description: str, fact_context: str
) -> list[str]:
    """Return added words that the job description and approved facts do not both support.

    Comparison is by stem, so an inflected form of an existing fact ("build" for
    "built") is the same claim rather than a new one.
    """
    original_stems = stems(original)
    description_stems = stems(job_description)
    supported_stems = stems(fact_context)
    return sorted(
        word
        for word in set(words(replacement))
        if stem(word) not in original_stems
        and (stem(word) not in description_stems or stem(word) not in supported_stems)
    )

