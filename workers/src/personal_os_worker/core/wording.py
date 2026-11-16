"""The words of the press, shared by the `--submit` run and the device's drive channel.

`jobs/` and `device/` both read them, and `device/` never imports `jobs/`, so
they live here. `SUBMIT_CONTROL` names a control that may send an application;
nothing forwards a click on one, and only the press paths press it.
`CAPTCHA_REJECTED` is what an ATS says when its invisible captcha refused the
press, seeded with Lever's banner; add phrases from captured pages.
"""

from __future__ import annotations

import re

SUBMIT_CONTROL = re.compile(
    r"^\s*submit\s*$|\b(?:submit|send|complete|finish)\b.*\b(?:application|form)\b",
    re.IGNORECASE,
)
CAPTCHA_REJECTED = re.compile(
    r"there\s+was\s+an\s+error\s+verifying\s+your\s+application"
    r"|\berror\s+verifying\s+your\s+(?:application|submission)\b",
    re.IGNORECASE,
)
