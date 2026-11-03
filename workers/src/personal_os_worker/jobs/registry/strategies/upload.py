"""Upload strategies: a file into a file input or its closed panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Error, Page

from personal_os_worker.core.page_scripts import load
from personal_os_worker.jobs.registry.strategies import CONTROL_TIMEOUT_MS, Attempt

# Whether a file control's upload is something you can see: its file is chosen,
# and the control sits in a rendered part of the page (a styled-over input in a
# visible dropzone counts) or the file's name shows. A file in a panel nobody
# opened — JazzHR's "Attach resume" — is not shown to you, whatever the input holds.
UPLOAD_SHOWN = load("upload_shown")

# The one control that opens a file input's closed panel: a visible link or
# button saying attach or upload in the field around it, which holds no other
# file input. Never one that navigates, submits, pastes, or reaches a cloud drive.
UPLOAD_OPENER = load("upload_opener")
FILE_CHOOSER_WAIT_MS = 1_000


def upload_into(page: Page, control: Any, document: Path) -> None:
    """Choose a file the way you would: open the field's panel first when it is shut.

    An opener that raises the browser's file dialog is answered with the file,
    so no dialog ever waits in your window.
    """
    opener = control.evaluate_handle(UPLOAD_OPENER, timeout=CONTROL_TIMEOUT_MS).as_element()
    if opener is not None:
        try:
            with page.expect_file_chooser(timeout=FILE_CHOOSER_WAIT_MS) as chooser:
                opener.click(timeout=CONTROL_TIMEOUT_MS)
            chooser.value.set_files(str(document), timeout=CONTROL_TIMEOUT_MS)
            return
        except Error:
            pass  # no dialog came (the panel opened, or the click did not take)
    control.set_input_files(str(document), timeout=CONTROL_TIMEOUT_MS)


def _upload_file(attempt: Attempt) -> str | None:
    upload_into(attempt.page, attempt.control, cast(Path, attempt.document))
    attempt.page.wait_for_timeout(750)
    return None


def _verify_upload(page: Page, field: dict[str, Any], filename: str | None) -> bool:
    """Whether a resume/transcript upload took.

    Greenhouse's own resume widget removes the `<input type=file>` from the
    DOM on a successful upload and replaces it with a filename-plus-remove-
    button summary, shifting every control after it by one position. Asking
    `fresh_control` for the same field afterward silently lands on whatever
    control now sits at that stale index instead of raising, so a real
    success reads as an empty, unrelated control. When the direct file-count
    check comes back empty, look for the uploaded filename rendered anywhere
    on the page before calling it a failure. A file the input holds in a panel
    nobody opened is not shown to you, so it does not count (`UPLOAD_SHOWN`).
    """
    from personal_os_worker.jobs.browser import fresh_control

    try:
        control = fresh_control(page, field)
        if control.evaluate(UPLOAD_SHOWN, timeout=CONTROL_TIMEOUT_MS) is True:
            return True
    except (Error, RuntimeError):
        pass
    if not filename:
        return False
    # The filename shows in the field's own frame (Greenhouse embedded on an
    # employer's site lives in an iframe).
    frame_index = int(field.get("frame_index") or 0)
    frame = page.frames[frame_index] if frame_index < len(page.frames) else page.main_frame
    try:
        return frame.get_by_text(filename, exact=False).first.is_visible()
    except Error:
        return False
