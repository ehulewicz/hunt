"""Long-form drafts, the reference material they lean on, and the cover letter."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    is_cover_letter_field,
    is_long_form_field,
)
from personal_os_worker.jobs.browser import (
    fresh_control,
    verify_control,
)
from personal_os_worker.jobs.compose import Posting, fact_rows
from personal_os_worker.jobs.cover_letter import (
    placeholder_slots,
    render_letter,
)
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.strategies.native import set_registered_value
from personal_os_worker.jobs.registry.strategies.upload import upload_into
from personal_os_worker.jobs.safety import armed
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    ARTIFACT_BUILD_ERRORS,
    echo,
    error_note,
)

SIMILAR_THRESHOLD = 0.6
SIMILAR_LIMIT = 5


def question_similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None, normalize_answer_question(left), normalize_answer_question(right)
    ).ratio()


def similar_answers(
    question: str,
    answers: list[dict[str, Any]],
    *,
    threshold: float = SIMILAR_THRESHOLD,
    limit: int = SIMILAR_LIMIT,
) -> list[dict[str, Any]]:
    """Rank stored prose whose question resembles this one, best first.

    The exact match ranks first at ratio 1.0: nothing is seeded from prose any
    more, so this list is reference material only.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for answer in answers:
        other = str(answer.get("question") or answer.get("normalized_question") or "")
        if not other or not str(answer.get("answer_text") or "").strip():
            continue
        score = question_similarity(question, other)
        if score >= threshold:
            scored.append((score, answer))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [answer for _, answer in scored[:limit]]


class WritingMixin(SessionBase):
    # ----- long-form drafts ------------------------------------------------

    def references(self, question: str) -> list[dict[str, Any]]:
        """Stored prose on similar questions, best first: reference for the
        fallback drafter only, never filled as is."""
        key = normalize_answer_question(question)
        if key in self.state.similar_prose:
            return self.state.similar_prose[key]
        writing = [
            {"question": item.get("question_label"), "answer_text": item.get("text")}
            for item in self.ctx.knowledge.prose
        ]
        self.state.similar_prose[key] = similar_answers(question, writing)
        return self.state.similar_prose[key]

    def seed_for(self, question: str, *, limit: int | None = None) -> tuple[str, str, str, str]:
        """Return (seed text, seed source, resolver id, human note)."""
        if not self.ctx.use_claude:
            return "", "empty", "", "drafting disabled; write it yourself"
        if question in self.state.drafted:
            return "", "empty", "", "a draft was already tried this run; answer still needed"
        self.state.drafted.add(question)
        composed = self._compose_seed(question, limit)
        if composed is not None:
            return composed
        references = self.references(question)
        draft = self.call_model(self.ctx.drafter,
            question=question,
            base=None,
            posting={
                "company": self.ctx.company,
                "role": self.ctx.title,
                "description": self.ctx.description,
            },
            resume=self.ctx.resume_json,
            facts=self.ctx.knowledge.values(),
            references=references,
            limit=limit,
            attempts=1,
        )
        if draft:
            self.state.origins[question] = (
                draft, tuple(str(item.get("answer_text") or "") for item in references)
            )
            return draft, "model", "model:long_form_v1", "claude draft tailored to this posting"
        return (
            "", "empty", "",
            "the library did not cover it and no claude draft came back; write it yourself",
        )

    def _compose_seed(
        self, question: str, limit: int | None
    ) -> tuple[str, str, str, str] | None:
        """One answer composed from the narrative library, or None when it does not cover it."""
        if not self.ctx.library:
            return None
        composed = self.call_model(
            self.ctx.composer,
            question=question,
            max_length=int(limit) if limit is not None else None,
            posting=Posting(self.ctx.company, self.ctx.title, self.ctx.description or ""),
            # Rebuilt per call: a fact set mid-run rebuilds this knowledge.
            facts=fact_rows(self.ctx.knowledge),
            career=self.ctx.resume_json,
            library=self.ctx.library,
            attempts=2,
        )
        if (
            composed is None
            or composed.composition is None
            or composed.composition.coverage == "missing"
            or not composed.composition.answer.strip()
        ):
            return None
        composition = composed.composition
        return (
            composition.answer,
            "prose",
            "library:" + ",".join(composition.sources),
            "composed from library " + ", ".join(composition.sources),
        )

    def fill_long_form(self, fields: list[dict[str, Any]]) -> None:
        """Seed every prose control with its best answer and say which deserve a look.

        An answer composed from the narrative library goes in first. Otherwise
        a Claude draft grounded in the facts and the whole career record goes in
        and is flagged for review. A control that already holds text is kept,
        and one with nothing to offer is left alone.
        """
        for item in fields:
            if not is_long_form_field(item):
                continue
            if is_cover_letter_field(item):
                continue  # fill_cover_letter owns it
            question = str(item.get("label") or "").strip()
            if not question:
                continue
            if item.get("filled") is True:
                echo("kept", question, note="already has text")
                continue
            if question in self.state.tier_answers or question in self.state.blanks:
                continue  # a pattern answered it from a fact this step
            limit = item.get("max_length")
            seed, source, resolver_id, note = self.seed_for(question, limit=limit)
            if seed and limit is not None and len(seed) > int(limit):
                # Writing past the cap registers and reads back, but the form
                # rejects it on submit. Say so rather than send it.
                echo(
                    "left",
                    question,
                    note=f"best answer runs {len(seed)} characters; the form caps it at {limit}",
                )
                continue
            self.state.seeds[question] = (seed, source)
            self.state.seed_resolver[question] = resolver_id
            if not seed:
                echo("left", question, note=note.split(";", 1)[0])
                continue
            try:
                with armed(self.page):
                    set_registered_value(fresh_control(self.page, item), seed)
                    ok = verify_control(self.page, item, seed)
            except (Error, RuntimeError) as error:
                ok = False
                echo("failed", question, note=str(error))
            if not ok:
                self.report_page_failure(
                    "readback_mismatch", f"Long-form answer for {question!r} did not register"
                )
                continue
            key = f"{self.state.step}:{item.get('fingerprint') or ''}#{item['index']}"
            self.state.resolutions[key] = AnswerResolution(seed, source, resolver_id)
            self.state.announced[key] = seed
            label = "claude draft" if source == "model" else "library"
            echo("long-form", question, seed, note=label)
            if source == "model":
                self.state.review.append((question, label))

    # ----- cover letter ----------------------------------------------------

    def letter_directory(self) -> Path:
        if self.ctx.letter_dir is not None:
            return self.ctx.letter_dir
        from personal_os_worker.core.service import state_dir

        return state_dir() / "cover-letters" / self.ctx.run_id

    def cover_letter_text(self) -> str:
        """Draft the slots once, open the letter in VS Code, and keep what the user left."""
        if self.state.cover_letter is not None:
            return self.state.cover_letter
        template = self.ctx.letter_template()
        posting = {
            "company": self.ctx.company,
            "role": self.ctx.title,
            "description": self.ctx.description,
        }
        slots = None
        if self.ctx.use_claude:
            console.print("  [dim]drafting the cover letter slots[/dim]")
            slots = self.call_model(
                self.ctx.letter_drafter, template, posting=posting, resume=self.ctx.resume_json
            )
        if slots is None:
            slots = placeholder_slots(template)
            note = "slots left as [prompts]; fill them in"
        else:
            note = f"{slots.primary_module}" + (
                f" + {slots.secondary_module}" if slots.secondary_module else ""
            )
        text = render_letter(template, slots, company=self.ctx.company, role=self.ctx.title)
        directory = self.letter_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cover_letter.md"
        path.write_text(text, encoding="utf-8")
        echo("letter", "Cover letter", note=note)
        console.print("  [dim]Cover letter ready for review[/dim]")
        self.ctx.editor(path)
        self.state.cover_letter = path.read_text(encoding="utf-8").strip() + "\n"
        return self.state.cover_letter

    def fill_cover_letter(self, fields: list[dict[str, Any]]) -> None:
        """Fill every cover-letter control on the page: textareas get the text, uploads the PDF."""
        targets = [
            item
            for item in fields
            if is_cover_letter_field(item) and item.get("filled") is not True
        ]
        if not targets:
            return
        text = self.cover_letter_text()
        if not text.strip():
            echo("left", "Cover letter", note="not reviewed; left for you")
            return
        for item in targets:
            question = str(item.get("label") or "Cover letter").strip()
            upload = str(item.get("type") or "").casefold() == "file"
            pdf: Path | None = None
            if upload:
                # Rendering the PDF is not a browser action. Keep its failure
                # apart from a readback mismatch: "I never produced a file" and
                # "the DOM refused my value" need different responses.
                try:
                    pdf = self.ctx.letter_renderer(
                        self.ctx.api,
                        self.ctx.resume_json.get("contact", {}),
                        text,
                        self.letter_directory(),
                        company=self.ctx.company,
                    )
                except ARTIFACT_BUILD_ERRORS as error:
                    note = error_note(error)
                    echo("failed", question, note=note)
                    self.report_page_failure(
                        "artifact_build_failed",
                        f"Cover letter PDF for {question!r} was never built: {note}",
                    )
                    continue
            shown = ""
            try:
                with armed(self.page):
                    if pdf is not None:
                        upload_into(self.page, fresh_control(self.page, item), pdf)
                        self.page.wait_for_timeout(750)
                        ok = verify_control(self.page, item, None, resume=True, filename=pdf.name)
                        shown = pdf.name
                    else:
                        set_registered_value(fresh_control(self.page, item), text)
                        ok = verify_control(self.page, item, text)
                        shown = text.strip()
                        self.state.seeds[question] = (shown, "model")
            except (Error, RuntimeError) as error:
                ok = False
                echo("failed", question, note=error_note(error))
            if ok:
                letter_key = f"{self.state.step}:{item.get('fingerprint') or ''}#{item['index']}"
                self.state.announced[letter_key] = shown
                echo("letter", question, shown)
            else:
                self.report_page_failure(
                    "readback_mismatch", f"Cover letter for {question!r} did not register"
                )
