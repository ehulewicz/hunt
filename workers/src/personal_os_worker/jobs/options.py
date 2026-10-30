"""Matching an answer to the options a control offers, and normalizing questions.

Shared by the planner, the formatters, and the browser strategies: which one
option an answer denotes ("Male" -> "Cisgender man", "3.78" -> "3.6-3.8",
"Fall 2027" -> "Autumn 2027"), and the normalizations questions are keyed by.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from personal_os_worker.jobs.sites import option_match

# A phone's country, as a menu of calling codes words it: "+1",
# "United States (+1)", "🇺🇸 (+1) United States of America".
CALLING_CODE = re.compile(r"\(?\+\s?(\d{1,4})\b\)?")


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_answer_question(value: str) -> str:
    """Normalize harmless punctuation differences for exact answer reuse."""
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


# Marks a form puts on a label that are not part of the question: a required
# star, a colon, a question mark, a trailing "(optional)" or "(please note...)".
# The backend keeps an identical copy in jobs_answers.py.
TRAILING_MARKS = re.compile(r"(?:\s*(?:[*\u2731:?]|\([^()]*\)))+\s*$")


def stripped_label(label: str) -> str:
    """The label without its trailing marks; itself when only marks remain."""
    stripped = label.strip()
    while True:
        shortened = TRAILING_MARKS.sub("", stripped).strip()
        if shortened == stripped:
            return shortened or label.strip()
        stripped = shortened


def label_forms(label: str) -> tuple[str, ...]:
    """The label as written, then without trailing marks when that differs."""
    as_written = label.strip()
    bare = stripped_label(label)
    return (as_written,) if as_written == bare else (as_written, bare)


def expression_matches(regex: re.Pattern[str], label: str) -> bool:
    """Whether an expression matches the label or its stripped form."""
    return any(regex.search(form) for form in label_forms(label))



def same_name(option: str, answer: str) -> bool:
    """Whether two technology names are the same ("Typescript"/"TypeScript", "React"/"React.js")."""

    def key(value: str) -> str:
        text = normalize_answer_question(value).replace(" ", "")
        return text.removesuffix("js") if len(text) > 3 else text

    return key(option) == key(answer)


def choose_option(options: list[str], answer: str) -> str | None:
    """The single option an answer denotes, or None when none or several fit."""
    matched = option_match(options, answer)
    if matched is not None:
        return matched
    # A prefix in whole words: "Yes" names "Yes - previous intern", but "No"
    # never names "Not Applicable" or "North Carolina".
    target = normalize_answer_question(answer).split()

    def leads(longer: list[str], shorter: list[str]) -> bool:
        return bool(shorter) and longer[: len(shorter)] == shorter

    prefixed = [
        option
        for option in options
        if leads(normalize_answer_question(option).split(), target)
        or leads(target, normalize_answer_question(option).split())
    ]
    if len(prefixed) == 1:
        return prefixed[0]
    seasonal = normalize_answer_question(answer).replace("autumn", "fall")
    seasonal_hits = [
        option
        for option in options
        if normalize_answer_question(option).replace("autumn", "fall") == seasonal
    ]
    if len(seasonal_hits) == 1:
        return seasonal_hits[0]
    return choose_numeric_range(options, answer) or choose_synonym(options, answer)


# Forms word the same identity differently; the profile holds one word.
SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"male", "man", "cis male", "cis man", "cisgender male", "cisgender man"}),
    frozenset(
        {"female", "woman", "cis female", "cis woman", "cisgender female", "cisgender woman"}
    ),
    frozenset({"non binary", "nonbinary", "genderqueer", "gender non conforming"}),
    frozenset(
        {
            "us citizen", "u s citizen", "united states citizen", "citizen of the united states",
            "citizen of the us", "citizen of the u s", "american citizen",
            "us citizen or national", "u s citizen or national",
        }
    ),
    frozenset(
        {
            "bs", "b s", "bsc", "b sc", "bachelor", "bachelors", "bachelor s", "bachelor degree",
            "bachelors degree", "bachelor s degree", "bachelor of science", "bachelor s of science",
            "bachelor of science bs", "bachelor of science b s", "undergraduate degree",
        }
    ),
    frozenset(
        {
            "straight", "heterosexual", "heterosexual or straight", "straight or heterosexual",
            "heterosexual straight", "straight heterosexual",
        }
    ),
    frozenset({"he him", "he him his", "he his", "him his", "he"}),
    frozenset(
        {"united states", "united states of america", "usa", "u s a", "us", "u s"}
    ),
)
QUALIFIER_WORDS = frozenset(
    {"cis", "cisgender", "he", "him", "his", "she", "her", "hers", "they", "them"}
)


def choose_synonym(options: list[str], answer: str) -> str | None:
    """The single option that means the same as the answer ("Male" -> "Cisgender man")."""
    target = normalize_answer_question(answer)
    group = next((group for group in SYNONYM_GROUPS if target in group), None)
    if group is None:
        return None
    hits: list[str] = []
    for option in options:
        # A calling code beside a country's name ("United States of America (+1)")
        # qualifies nothing.
        words = normalize_answer_question(CALLING_CODE.sub(" ", option)).split()
        for length in range(len(words), 0, -1):
            head, tail = " ".join(words[:length]), words[length:]
            if head in group and all(word in QUALIFIER_WORDS for word in tail):
                hits.append(option)
                break
    return hits[0] if len(hits) == 1 else None


NUMBER = r"(\d+(?:\.\d+)?)"
RANGE_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[float, float, float], bool]], ...] = (
    (
        re.compile(rf"^{NUMBER}\s*(?:-|\u2013|\u2014|to)\s*{NUMBER}\b"),
        lambda v, lo, hi: lo <= v <= hi,
    ),
    (
        re.compile(rf"^{NUMBER}\s*(?:\+|or (?:above|higher|more|greater)|and (?:above|up))"),
        lambda v, lo, _: v >= lo,
    ),
    (
        re.compile(rf"^(?:above|over|more than|greater than)\s*{NUMBER}"),
        lambda v, lo, _: v > lo,
    ),
    (
        re.compile(rf"^(?:below|under|less than)\s*{NUMBER}"),
        lambda v, hi, _: v < hi,
    ),
    (
        re.compile(rf"^{NUMBER}\s*(?:or (?:below|less|lower)|and (?:below|under))"),
        lambda v, hi, _: v <= hi,
    ),
    (re.compile(rf"^(?:<=|\u2264)\s*{NUMBER}"), lambda v, hi, _: v <= hi),
    (re.compile(rf"^<\s*{NUMBER}"), lambda v, hi, _: v < hi),
    (re.compile(rf"^(?:>=|\u2265)\s*{NUMBER}"), lambda v, lo, _: v >= lo),
    (re.compile(rf"^>\s*{NUMBER}"), lambda v, lo, _: v > lo),
)


def _option_numbers(option: str) -> list[float]:
    """The numbers an option carries, a k suffix counting in thousands."""
    text = re.sub(r"[$,]|\busd\b", "", normalize_question(option))
    scale = 1000 if re.search(rf"{NUMBER}\s*k\b", text) else 1
    return [float(number) * scale for number in re.findall(NUMBER, text)]


def choose_numeric_range(options: list[str], answer: str) -> str | None:
    """The single range option a number falls in ("3.78" -> "3.6-3.8"), else None."""
    try:
        value = float(re.sub(r"[^\d.]", "", answer.strip()) or "x")
    except ValueError:
        return None
    hits: list[str] = []
    for option in options:
        # "$30-$45" and "$20,000 - $30,000" are ranges too; a k suffix counts
        # every bound in thousands ("26-30k" is 26000-30000).
        text = re.sub(r"[$,]|\busd\b", "", normalize_question(option)).strip()
        scale = 1000 if re.search(rf"{NUMBER}\s*k\b", text) else 1
        text = re.sub(rf"({NUMBER})\s*k\b", r"\1", text)
        for pattern, holds in RANGE_PATTERNS:
            found = pattern.match(text)
            if found is None:
                continue
            bounds = [
                float(group) * scale for group in found.groups() if group is not None
            ]
            low = bounds[0]
            high = bounds[1] if len(bounds) > 1 else bounds[0]
            if holds(value, low, high):
                hits.append(option)
            break
    if len(hits) == 1:
        return hits[0]
    # A lone bare number at the top of the scale ("3.75" above "3.4 - 3.70")
    # is its open-ended top when nothing holds the value but the value clears it.
    bare = [
        option
        for option in options
        if re.fullmatch(
            rf"\s*[$]?\s*{NUMBER}\s*k?\s*",
            re.sub(r"[,]", "", normalize_question(option)),
        )
    ]
    if len(bare) != 1:
        return None
    option = bare[0]
    number = _option_numbers(option)[0]
    if number <= value and all(
        number >= other_number
        for other in options
        if other != option
        for other_number in _option_numbers(other)
    ):
        return option
    return None
