"""The narrative library in the worker: the question-bank parser and its CLI.

`os hunt library import FILE` loads a question bank in markdown once into an
empty library; `os hunt library get|put` reads and writes it through the API.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from rich.text import Text

from personal_os_worker.core.config import console
from personal_os_worker.jobs.facts.career import rejection

if TYPE_CHECKING:
    from personal_os_worker.core.api import WorkerApi

SECTION_MAP = {"1": "interest", "2": "direction", "3": "evidence",
               "4": "story", "5": "entry", "6": "entry", "7": "note"}
SECTION_HEADING = re.compile(r"^## (\d+)\.\s")
ENTRY_HEADING = re.compile(r"^#{3,4} (\d+(?:\.\d+)+)\s+(.+)$")
ANY_HEADING = re.compile(r"^#{2,4} ")
LABEL_HEADING = re.compile(r"^#### ([A-Z]\d+)\s*$")
LABEL_BULLET = re.compile(r"^[-*]\s*([A-Z]\d+):\s*(.*)$")


def _bullets(lines: list[str]) -> dict[str, str]:
    """A bullet list of `- E1: text` items; indented continuations join the item."""
    items: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = LABEL_BULLET.match(line)
        if match:
            current = match.group(1)
            items[current] = [match.group(2).strip()]
        elif current and (line.startswith(" ") or line.startswith("\t")) and line.strip():
            items[current].append(line.strip())
        elif line.strip():
            current = None
    return {key: " ".join(parts).strip() for key, parts in items.items()}


def _split_answer(body: str) -> tuple[str, str | None]:
    """A body's text and its `Answer:` block, when one is there and non-empty."""
    match = re.search(r"(?m)^Answer:\s*", body)
    if not match:
        return body.strip(), None
    answer = body[match.end():].strip()
    return body[: match.start()].strip(), answer or None


def parse_bank(path: Path) -> list[dict[str, Any]]:
    """The bank as API write bodies (entry_id left for link_entries to fill)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    # Sections: a `## N.` heading starts one; everything before the first is ignored.
    sections: list[tuple[str, str, int]] = []  # (section number, title, line index)
    for index, line in enumerate(lines):
        match = SECTION_HEADING.match(line)
        if match:
            sections.append((match.group(1), line, index))
    entries: list[dict[str, Any]] = []
    for pos, (number, _line, start) in enumerate(sections):
        section = SECTION_MAP.get(number)
        end = sections[pos + 1][2] if pos + 1 < len(sections) else len(lines)
        if section is None:
            continue
        block = lines[start + 1 : end]
        # The intro is the text before the first ### heading inside the section.
        first_entry = next(
            (i for i, line in enumerate(block) if re.match(r"^#{3,4} ", line)), len(block)
        )
        intro = "\n".join(block[:first_entry]).strip()
        if section == "entry":
            _experience_entries(entries, section, intro, block)
        else:
            _plain_entries(entries, section, intro, block)
    return entries


def _plain_entries(
    entries: list[dict[str, Any]], section: str, intro: str, block: list[str]
) -> None:
    """One entry per `### x.y` heading; 2.2's bullet items become 2.2.N entries."""
    boundaries = [i for i, line in enumerate(block) if re.match(r"^#{3,4} ", line)]
    boundaries.append(len(block))
    for span in range(len(boundaries) - 1):
        head, tail = boundaries[span], boundaries[span + 1]
        match = ENTRY_HEADING.match(block[head])
        body_lines = block[head + 1 : tail]
        if not match:
            continue
        code, prompt = match.group(1), match.group(2).strip()
        body, answer = _split_answer("\n".join(body_lines))
        extra: list[dict[str, Any]] = []
        explicit_children = {
            match.group(1)
            for i in boundaries[:-1]
            if (match := ENTRY_HEADING.match(block[i])) is not None
        }
        if code == "2.2" and not any(
            child.startswith("2.2.") for child in explicit_children
        ):
            items = [line for line in body_lines
                     if re.match(r"^[-*]\s+", line)]
            body_lines_kept = [line for line in body_lines
                               if not re.match(r"^[-*]\s+", line)]
            body, answer = _split_answer("\n".join(body_lines_kept))
            for index, item in enumerate(items, start=1):
                name = re.sub(r"^[-*]\s+", "", item).strip()
                extra.append({
                    "code": f"2.2.{index}", "section": "direction", "entry_id": None,
                    "subject": name, "prompt": f"What draws you to {name}",
                    "guidance": "Two to four sentences, with evidence.",
                    "answer": None, "linked_heading": None,
                })
        guidance = body + ("\n\n" + intro if intro else "") if body else intro
        if re.match(r"^\d+\.\d+\.\d+$", code) and section == "direction":
            entries.append({
                "code": code, "section": section, "entry_id": None,
                "subject": prompt, "prompt": f"What draws you to {prompt}",
                "guidance": "Two to four sentences, with evidence.",
                "answer": answer, "linked_heading": None,
            })
        else:
            entries.append({
                "code": code, "section": section, "entry_id": None,
                "subject": None, "prompt": prompt, "guidance": guidance,
                "answer": answer, "linked_heading": None,
            })
        entries.extend(extra)


def _experience_entries(
    entries: list[dict[str, Any]], section: str, intro: str, block: list[str]
) -> None:
    """Sections 5 and 6: `###` parents; `#### E1`/`#### P2` children are the entries."""
    labels = _bullets(intro.splitlines())
    heads = [i for i, line in enumerate(block) if re.match(r"^#{3,4} ", line)]
    heads.append(len(block))
    parent_title = parent_body_text = parent_code = ""
    for span in range(len(heads) - 1):
        head, tail = heads[span], heads[span + 1]
        line = block[head]
        parent_match = re.match(r"^### (\d+\.\d+)\s+(.+)$", line)
        if parent_match:
            parent_code, parent_title = parent_match.group(1), parent_match.group(2).strip()
            parent_body_text = "\n".join(block[head + 1 : tail])
            continue
        match = LABEL_HEADING.match(line)
        if match is None:
            continue
        parent_guidance = re.split(r"(?m)^#{3,4} ", parent_body_text)[0]
        _body, answer = _split_answer("\n".join(block[head + 1 : tail]))
        prompt = labels.get(match.group(1), "")
        if prompt:
            prompt = prompt[0].upper() + prompt[1:]
        entries.append({
            "code": f"{parent_code}.{match.group(1)}", "section": section,
            "entry_id": None, "subject": parent_title, "prompt": prompt,
            "guidance": parent_guidance.strip(), "answer": answer,
            "linked_heading": parent_title,
        })


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", "", text.casefold().replace("@", " ")).split()


def link_entries(
    entries: list[dict[str, Any]], career_document: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Attach the career entry each 5.x/6.x library entry belongs to, by heading."""
    candidates = {
        "5": [*(career_document.get("experience") or []),
              *(career_document.get("research") or [])],
        "6": [*(career_document.get("projects") or []),
              *(career_document.get("activities") or [])],
    }
    warnings: list[str] = []
    for entry in entries:
        section_number = entry["code"].split(".", 1)[0]
        heading = entry.get("linked_heading")
        if section_number not in candidates or not heading:
            continue
        heading_words = _words(str(heading))
        matched = []
        for candidate in candidates[section_number]:
            names = [candidate.get(key) for key in ("org", "employer", "title")]
            if any(name and heading_words[: len(_words(str(name)))] == _words(str(name))
                   for name in names):
                matched.append(candidate)
        if len(matched) == 1:
            entry["entry_id"] = matched[0].get("id")
        else:
            if not matched:
                warnings.append(f"{entry['code']}: no career entry matches '{heading}'")
            else:
                matched_names = ", ".join(
                    str(item.get("title") or item.get("org")) for item in matched
                )
                warnings.append(
                    f"{entry['code']}: matches several career entries: {matched_names}"
                )
    return entries, warnings


def write_body(entry: dict[str, Any]) -> dict[str, Any]:
    """The API's write body: parser fields minus the worker-only link heading."""
    return {
        "code": entry["code"], "section": entry["section"],
        "entry_id": entry.get("entry_id"), "subject": entry.get("subject"),
        "prompt": entry.get("prompt"), "guidance": entry.get("guidance"),
        "answer": entry.get("answer"), "display_order": entry["display_order"],
    }


def assign_order(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, entry in enumerate(entries):
        entry["display_order"] = index
    return entries


def run_library_get(api: WorkerApi) -> int:
    sys.stdout.write(json.dumps(api.library(), indent=2, ensure_ascii=False) + "\n")
    return 0


# Fields the API owns; a `library get` round trip strips them before `put`.
READ_ONLY = {"id", "created_at", "updated_at", "human_percent"}


def _human_cell(entry: dict[str, Any]) -> Text:
    """The entry's GPTZero share, styled by band; muted when there is none."""
    if not str(entry.get("answer") or "").strip():
        return Text("no answer", style="dim")
    percent = entry.get("human_percent")
    if percent is None:
        return Text("unchecked", style="dim")
    style = "green" if percent >= 80 else "yellow" if percent >= 50 else "red"
    return Text(f"{percent}%", style=style)


def run_library_list(api: WorkerApi) -> int:
    entries = api.library()
    checked = below = 0
    for entry in entries:
        answer = str(entry.get("answer") or "").strip()
        percent = entry.get("human_percent")
        if answer and percent is not None:
            checked += 1
            below += percent < 80
        prompt = " ".join(str(entry.get("prompt") or "").split())
        width = max(20, console.width - 60)
        if len(prompt) > width:
            prompt = prompt[: width - 1] + "…"
        line = Text(
            f"{entry['code']:<10} {entry['section']:<9} "
            f"{len(answer.split()):>4}w  "
        )
        line.append_text(_human_cell(entry))
        line.append(f"  {prompt}")
        console.print(line)
    console.print(
        f"{len(entries)} entries · {checked} checked · "
        f"{below} below 80% human · {len(entries) - checked} unchecked"
    )
    return 0


def run_library_score(api: WorkerApi, code: str, percent: int) -> int:
    try:
        entry = api.set_library_human_percent(code, percent)
    except httpx.HTTPStatusError as error:
        console.print(f"Score not saved: {rejection(error)}")
        return 1
    console.print(f"{entry['code']}: {entry['human_percent']}% human")
    return 0


def run_library_put(api: WorkerApi, file: str) -> int:
    try:
        payload = json.loads(Path(file).expanduser().read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        console.print(
            f"Library JSON is invalid at line {error.lineno}, column {error.colno}: {error.msg}"
        )
        return 1
    try:
        if isinstance(payload, list):
            api.put_library([
                {key: value for key, value in entry.items() if key not in READ_ONLY}
                for entry in payload
            ])
        elif isinstance(payload, dict):
            code = str(payload.get("code") or "")
            api.put_library_entry(
                code,
                {key: value for key, value in payload.items() if key not in READ_ONLY},
            )
        else:
            raise RuntimeError("Library JSON must be an object or a list")
    except httpx.HTTPStatusError as error:
        console.print(f"Library not saved: {rejection(error)}")
        return 1
    console.print("Library saved.")
    return 0


def run_library_import(
    api: WorkerApi, file: str, *, dry_run: bool = False
) -> int:
    entries, warnings = link_entries(
        assign_order(parse_bank(Path(file).expanduser())), api.career().get("document") or {}
    )
    for warning in warnings:
        console.print(f"  [yellow]{warning}[/yellow]")
    if dry_run:
        console.print(" ".join(entry["code"] for entry in entries))
        console.print(f"{len(entries)} entries parsed; nothing imported (dry run).")
        return 0
    bodies = [write_body(entry) for entry in entries]
    try:
        imported = api.import_library(bodies)
    except httpx.HTTPStatusError as error:
        console.print(f"Library not saved: {rejection(error)}")
        return 1
    answered = sum(1 for entry in imported if entry.get("answer"))
    console.print(f"Imported {len(imported)} library entries; {answered} have answers.")
    return 0
