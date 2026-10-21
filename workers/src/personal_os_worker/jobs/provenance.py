"""Provenance: where each stretch of a long-form answer came from.

Prose is saved at the end of every run as one text. A stretch that matches a
previous answer the writing started from (approved prose reused as it was, or
the approved writing a draft was given as reference) is `previous`; one that
matches the model's draft is `model`; anything in neither is yours and gets no
span. A previous answer wins where both match, since the draft took it from
there. Matches shorter than a few words are coincidence, not borrowing.
"""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Literal

Source = Literal["model", "previous"]
# A shared stretch shorter than this is common words, not borrowed text.
MIN_SPAN = 16


def spans(final: str, *, draft: str = "", previous: Sequence[str] = ()) -> list[dict[str, object]]:
    """Ordered, non-overlapping spans of `final`, each with where it came from."""
    marks: list[Source | None] = [None] * len(final)
    sources: list[tuple[Source, str]] = [("previous", text) for text in previous if text]
    if draft:
        sources.append(("model", draft))
    for source, text in sources:
        matcher = SequenceMatcher(None, final, text, autojunk=False)
        for block in matcher.get_matching_blocks():
            if block.size < MIN_SPAN:
                continue
            for index in range(block.a, block.a + block.size):
                if marks[index] is None:
                    marks[index] = source
    found: list[dict[str, object]] = []
    start = 0
    for index in range(1, len(final) + 1):
        if index == len(final) or marks[index] != marks[start]:
            if marks[start] is not None:
                found.append({"start": start, "end": index, "source": marks[start]})
            start = index
    return found
