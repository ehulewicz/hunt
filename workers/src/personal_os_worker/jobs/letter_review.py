"""Where the filled cover letter goes for review: the Hunt page, or you.

No editor opens on this machine. A run this device's agent can see sends the
letter to the Hunt page and waits for "Use this letter"; anything else leaves
the letter file empty, which the run reads as "left for you": no field filled.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from personal_os_worker.core.tables import console
from personal_os_worker.jobs.cover_letter import PLACEHOLDERS

if TYPE_CHECKING:
    from personal_os_worker.jobs.remote import RunLink

# The bracketed prompts a model left unfilled; a letter holding one is never
# auto-accepted.
PROMPT_NAMES = ("hook", "why_this", "tie")


def review_letter(path: Path, *, link: RunLink | None, accept: bool) -> None:
    """Review the filled letter in `path`; whatever it holds afterwards is the letter.

    `accept` keeps the letter as filled when no bracketed prompt is left.
    A linked run sends it to the Hunt page and keeps what the page sends back.
    Anything else empties the file and leaves the letter for the page.
    """
    text = path.read_text(encoding="utf-8")
    prompts = [name for name in PROMPT_NAMES if PLACEHOLDERS[name] in text]
    if accept and not prompts:
        console.print("  [dim]cover letter accepted as filled[/dim]")
        return
    if link is not None and link.edit_letter(path):
        console.print("  [dim]cover letter reviewed on the Hunt page[/dim]")
        return
    path.write_text("", encoding="utf-8")
    if prompts:
        console.print(
            "  [bold]Cover letter left for you[/bold] — it still has \\[slot] prompts "
            "to finish on the Hunt page"
        )
    else:
        console.print(
            "  [bold]Cover letter left for you[/bold]: open this run on the Hunt page, "
            "or pass --accept-letter to use the filled letter as is"
        )
