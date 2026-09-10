from __future__ import annotations

import json
import subprocess
from typing import Any

from personal_os_worker.resume_guard import ResumeValidation, apply_and_validate, resume_text

PATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "patches": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"const": "replace"},
                    "path": {"type": "string"},
                    "value": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["op", "path", "value", "keywords", "reason"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["patches", "summary"],
}


def tailor_resume(
    main_resume: dict[str, Any],
    *,
    job_description: str,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
) -> tuple[ResumeValidation, dict[str, Any]]:
    fact_context = "\n".join(
        (
            resume_text(main_resume),
            json.dumps(profile, default=str),
            json.dumps(approved_answers, default=str),
        )
    )
    prompt = f"""
You are applying a MINIMAL ATS alignment patch to an approved resume.

Hard rules:
- Return patch operations only. Never return or rewrite the full resume.
- Preserve every section, role, company, project, date, education item, order, and bullet count.
- Only replace existing experience/project bullet text or skills item text.
- Do not invent, strengthen, or infer facts. A keyword is allowed only when the job description
  uses it and the approved facts below already support it.
- Prefer zero changes. Make only high-value keyword substitutions or short insertions.
- The final changed-word ratio must be at most 10 percent.
- Do not alter numbers or metrics.

Allowed JSON pointer paths:
/experience/<index>/bullets/<index>
/research/<index>/bullets/<index>
/projects/<index>/bullets/<index>
/skills/<index>/items

APPROVED RESUME:
{json.dumps(main_resume, indent=2)}

APPROVED PROFILE AND ANSWERS:
{json.dumps({"profile": profile, "answers": approved_answers}, indent=2, default=str)}

JOB DESCRIPTION:
{job_description[:30000]}
""".strip()
    process = subprocess.run(
        [
            "claude",
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(PATCH_SCHEMA, separators=(",", ":")),
            prompt,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or "Claude did not return a tailoring patch"
        raise RuntimeError(detail[:1000])
    output = json.loads(process.stdout)
    structured = output.get("structured_output", output)
    if not isinstance(structured, dict) or not isinstance(structured.get("patches"), list):
        raise RuntimeError("Claude response did not contain structured resume patches")
    validation = apply_and_validate(
        main_resume,
        structured["patches"],
        job_description=job_description,
        fact_context=fact_context,
    )
    return validation, structured
