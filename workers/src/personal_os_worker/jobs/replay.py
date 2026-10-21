"""Replay: a learned change checked offline against the answers you accepted (R69).

A sighting is a short question answered in an application that went in (the
API's `/v1/jobs/sightings`): its label, the control and the options it offered,
what the worker filled then (as a hash), and the answer submitted, which is the
answer you accepted. A change is replayed against the sightings it can affect
(those its question or expression matches, and those filled from its fact) and
the most recent and a random few besides. The resolver and formatters run as
they do in a fill: once as things stand, once with the change.

A sighting fails when the change gives it a different answer that is not the
one you accepted, or takes away the one you accepted. An answer is the one you
accepted when the fact it came from can be written that way for the control,
whichever writing the rebuilt control picks: a sighting from before options
were kept rebuilds a menu without them. A sighting the change leaves as it was
never fails, right or wrong: what was wrong before is not this change's doing.
A code change cannot be run as it was, so its before is what the worker filled
then, known only when that was the accepted answer or nothing at all, and
unknown for an answer from before fill events.

Pure: the session and the maintenance path fetch the sightings and record
what a replay found.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from personal_os_worker.jobs.formatters import BLANK, renderings
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.events import value_hash
from personal_os_worker.jobs.registry.kinds import BUTTON_GROUP
from personal_os_worker.jobs.resolve import Knowledge, Resolver

# Beyond the sightings a change can affect: the newest, and a random few.
RECENT = 25
RANDOM = 25
# A control's structure when a sighting kept only its registry kind, or only the
# input type or tag its answer recorded.
KIND_SHAPES: dict[str, dict[str, Any]] = {
    "select": {"tag": "select"},
    "text_entry": {"tag": "input", "type": "text"},
    "native_select": {"tag": "select"},
    "choice_group": {"tag": "input", "type": "radio"},
    "multi_choice_group": {"tag": "input", "type": "checkbox", "group": "many"},
    "toggle": {"tag": "input", "type": "checkbox"},
    "dynamic_choice": {"tag": "input", "role": "combobox"},
}
# Controls whose answers are not replayed: prose, and an upload.
NOT_REPLAYED = frozenset({"file", "textarea"})


@dataclass(frozen=True, slots=True)
class Sighting:
    question: str
    question_key: str
    kind: str | None
    attributes: dict[str, Any]
    # The input type or tag the answer recorded (`radio`, `select`, `text`).
    control: str | None
    options: tuple[str, ...]
    answer: str
    fact_key: str | None
    value_hash: str | None
    knowledge: dict[str, Any] | None = None

    @classmethod
    def of(cls, row: dict[str, Any]) -> Sighting:
        return cls(
            question=str(row.get("question") or ""),
            question_key=str(row.get("question_key") or ""),
            kind=str(row["kind"]) if row.get("kind") else None,
            attributes=dict(row.get("attributes") or {}),
            control=str(row["control"]) if row.get("control") else None,
            options=tuple(str(option) for option in row.get("options") or ()),
            answer=str(row.get("answer") or ""),
            fact_key=str(row["fact_key"]) if row.get("fact_key") else None,
            value_hash=str(row["value_hash"]) if row.get("value_hash") else None,
            knowledge=dict(row["knowledge"]) if row.get("knowledge") is not None else None,
        )

    def context(self, current: Knowledge) -> Knowledge:
        """Keep the code and patterns under test, using this attempt's original inputs."""
        if self.knowledge is None:
            return current
        return replace(
            current, facts=dict(self.knowledge["facts"]), types=dict(self.knowledge["types"]),
            seed=str(self.knowledge.get("seed") or ""),
        )

    def field(self) -> dict[str, Any]:
        """The control as the planner would hand it to the resolver."""
        control = self.control or ""
        shape = (
            self.attributes
            or KIND_SHAPES.get(self.kind or "")
            or KIND_SHAPES.get(control)
            or {"tag": "input", "type": control}
        )
        field_type = str(shape.get("type") or "")
        field: dict[str, Any] = {
            "label": self.question,
            "tag": str(shape.get("tag") or ""),
            "type": BUTTON_GROUP if field_type == "button_group" else field_type,
            "role": str(shape.get("role") or ""),
        }
        if shape.get("multiple") is True:
            field["multiple"] = True
        if shape.get("group") == "many":
            field["group_size"] = max(2, len(self.options))
        if self.options:
            field["options"] = list(self.options)
        return field


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", value.casefold())


def same_answer(value: str | None, answer: str, *, kind: str | None = None) -> bool:
    """Whether a replayed value says what was submitted, punctuation and case aside;
    a group of checkboxes as the same set of choices."""
    if value is None:
        return False
    if kind == "multi_choice_group":
        def parts(text: str) -> set[str]:
            return {_key(part) for part in re.split(r"[\n,;]", text) if _key(part)}

        return parts(value) == parts(answer)
    return _key(value) == _key(answer)


@dataclass(frozen=True, slots=True)
class Answer:
    """What a resolver writes into a control, and every other way its fact can be
    written for that control."""

    value: str
    writings: tuple[str, ...] = ()

    def says(self, answer: str, *, kind: str | None = None) -> bool:
        return any(same_answer(text, answer, kind=kind) for text in (self.value, *self.writings))


def answer_of(resolver: Resolver, sighting: Sighting) -> Answer | None:
    """What the resolver writes into this sighting's control: "" for a deliberate blank."""
    field = sighting.field()
    resolution = resolver.resolve(field)
    if resolution is None:
        return None
    value = "" if resolution.formatter == BLANK else resolution.value
    key, knowledge = resolution.fact_key, resolver.knowledge
    if key is None or key not in knowledge.facts:
        return Answer(value)
    return Answer(value, tuple(
        item.value
        for item in renderings(
            key, knowledge.facts[key], knowledge.types.get(key, "text"),
            field=field, question=sighting.question,
        )
    ))


@dataclass(frozen=True, slots=True)
class Outcome:
    sighting: Sighting
    before: Answer | None
    after: Answer | None
    # False when what was filled before is unknown, or known only to be wrong.
    before_known: bool = True

    @property
    def now(self) -> str | None:
        """What the control would hold with the change; None when nothing answers it."""
        return self.after.value if self.after is not None else None

    @property
    def failed(self) -> bool:
        answer, kind = self.sighting.answer, self.sighting.kind
        if (self.after is not None and self.after.says(answer, kind=kind)) or not self.before_known:
            return False
        if self.after is None:
            return self.before is not None and self.before.says(answer, kind=kind)
        if self.before is None:
            return True
        return not same_answer(self.before.value, self.after.value, kind=kind)


@dataclass(frozen=True, slots=True)
class Replay:
    outcomes: tuple[Outcome, ...]

    @property
    def failures(self) -> list[Outcome]:
        return [outcome for outcome in self.outcomes if outcome.failed]

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def unchecked(self) -> int:
        return sum(not outcome.before_known for outcome in self.outcomes)

    def verification(self) -> dict[str, int]:
        return {"sampled": len(self.outcomes) - self.unchecked, "failed": len(self.failures)}

    def summary(self) -> str:
        context = (
            f"; {self.unchecked} without original facts or a known baseline"
            if self.unchecked else ""
        )
        if self.passed:
            return (
                f"replayed {len(self.outcomes)} accepted answer(s), none changed for the worse"
                + context
            )
        worst = self.failures[0]
        return (
            f"{len(self.failures)} of {len(self.outcomes)} accepted answer(s) would change; "
            f"{worst.sighting.question.strip()!r} would get {worst.now!r}, "
            f"not {worst.sighting.answer!r}" + context
        )


def sightings_of(rows: Iterable[dict[str, Any]]) -> list[Sighting]:
    """Sightings a replay can run: an upload is never replayed."""
    return [
        sighting for sighting in map(Sighting.of, rows)
        if sighting.question.strip()
        and not {sighting.kind, sighting.control} & NOT_REPLAYED
    ]


def select(
    sightings: Sequence[Sighting],
    touches: Callable[[Sighting], bool],
    *,
    seed: str,
    recent: int = RECENT,
    sample: int = RANDOM,
) -> list[Sighting]:
    """Every sighting the change touches, the newest `recent`, and `sample` more at random.

    `sightings` is newest first. The random few are seeded by the change, so the
    same change replays the same sightings.
    """
    picked = [index for index, sighting in enumerate(sightings) if touches(sighting)]
    taken = set(picked)
    picked += [index for index in range(min(recent, len(sightings))) if index not in taken]
    taken.update(picked)
    rest = [index for index in range(len(sightings)) if index not in taken]
    picked += random.Random(seed).sample(rest, min(sample, len(rest)))
    return [sightings[index] for index in picked]


def touched_by(pattern: dict[str, Any]) -> Callable[[Sighting], bool]:
    """Whether a sighting is one this pattern can affect: its question or wording,
    or a question filled from its fact."""
    fact = str(pattern.get("fact_key") or "")
    if pattern.get("kind") == "binding":
        key = normalize_answer_question(str(pattern.get("question_key") or ""))
        return lambda sighting: (
            normalize_answer_question(sighting.question) == key or sighting.fact_key == fact
        )
    regex = re.compile(str(pattern.get("expression") or "(?!)"), re.IGNORECASE | re.DOTALL)
    return lambda sighting: (
        regex.search(sighting.question.strip()) is not None or sighting.fact_key == fact
    )


def replay_pattern(
    sightings: Sequence[Sighting], knowledge: Knowledge, pattern: dict[str, Any]
) -> Replay:
    """Replay the sightings a new active pattern can affect, before and with it."""
    seed = str(pattern.get("expression") or pattern.get("question_key") or "")
    chosen = select(sightings, touched_by(pattern), seed=f"{seed}->{pattern.get('fact_key')}")
    outcomes: list[Outcome] = []
    for sighting in chosen:
        context = sighting.context(knowledge)
        before = answer_of(Resolver(context), sighting)
        after = answer_of(Resolver(context.with_pattern(pattern)), sighting)
        # With no snapshot, today's fact is usable only when the unchanged
        # resolver still reproduces what the user accepted. Otherwise a changed
        # fact cannot be distinguished from a changed rule.
        known = sighting.knowledge is not None or (
            before is not None and before.says(sighting.answer, kind=sighting.kind)
        )
        outcomes.append(Outcome(sighting, before, after, before_known=known))
    return Replay(tuple(outcomes))


def replay_code(
    sightings: Sequence[Sighting],
    knowledge: Knowledge,
    *,
    question_key: str | None = None,
    fact_key: str | None = None,
    seed: str = "",
) -> Replay:
    """Replay the sightings the code as it stands answers, against what was filled then.

    The failure's question and fact are touched; a fill then that was the accepted
    answer must still be, and a question left alone then must not get a wrong one.
    """
    key = normalize_answer_question(question_key or "")

    def touches(sighting: Sighting) -> bool:
        return bool(
            (key and normalize_answer_question(sighting.question_key) == key)
            or (fact_key and sighting.fact_key == fact_key)
        )

    outcomes: list[Outcome] = []
    for sighting in select(sightings, touches, seed=seed or key):
        after = answer_of(Resolver(sighting.context(knowledge)), sighting)
        if sighting.kind is None or sighting.knowledge is None:
            # No fill event: what the worker did then was never recorded.
            outcomes.append(Outcome(sighting, None, after, before_known=False))
        elif sighting.value_hash is None:
            outcomes.append(Outcome(sighting, None, after))
        elif sighting.value_hash == value_hash(sighting.answer):
            outcomes.append(Outcome(sighting, Answer(sighting.answer), after))
        else:
            outcomes.append(Outcome(sighting, None, after, before_known=False))
    return Replay(tuple(outcomes))


def replay_fact(
    sightings: Sequence[Sighting], knowledge: Knowledge, key: str, value: Any,
    *, value_type: str | None = None,
) -> Replay:
    """Replay the sightings a fact answers with its new value instead of its old one.

    A sighting is touched when it was filled from the fact, or an active
    pattern answers its question from it. Each is answered once with the
    attempt's original facts and once with this fact changed, so only this
    change can make an accepted answer differ; a fact you set is yours, and
    what this finds is recorded beside it rather than refusing it.
    """

    def changed(context: Knowledge) -> Knowledge:
        types = dict(context.types)
        if value_type is not None:
            types[key] = value_type
        return replace(context, facts={**context.facts, key: value}, types=types)

    def touches(sighting: Sighting) -> bool:
        if sighting.fact_key == key:
            return True
        resolution = Resolver(sighting.context(knowledge)).resolve(sighting.field())
        return resolution is not None and resolution.fact_key == key

    outcomes: list[Outcome] = []
    for sighting in select(sightings, touches, seed=key, recent=0, sample=0):
        context = sighting.context(knowledge)
        before = answer_of(Resolver(context), sighting)
        after = answer_of(Resolver(changed(context)), sighting)
        known = sighting.knowledge is not None or (
            before is not None and before.says(sighting.answer, kind=sighting.kind)
        )
        outcomes.append(Outcome(sighting, before, after, before_known=known))
    return Replay(tuple(outcomes))
