"""Health checks for the document toolchain.

A missing TeX package is a machine-level fact, knowable before a browser opens,
and it fails every resume and cover-letter build. `core.doctor` owns the check
framework; what a document needs in order to compile is known only here.
"""

from __future__ import annotations

import shutil
import subprocess

from personal_os_worker.core.doctor import DoctorCheck, command_check

LATEX_CHECK = "LaTeX toolchain"


def latex_toolchain_check() -> DoctorCheck:
    """latexmk runs and TeX can find every file the resume/letter preamble loads.

    A missing package is a machine-level fact, knowable before a browser opens,
    and it fails every cover-letter build. Checking it here turns a mid-
    application surprise into a startup error.
    """
    from personal_os_worker.documents.resume_pdf import preamble_requirements

    binary = command_check(LATEX_CHECK, "latexmk", ["-v"])
    if not binary.ok:
        return binary
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich is None:
        return DoctorCheck(LATEX_CHECK, False, "kpsewhich is not installed or not on PATH")
    required = preamble_requirements()
    missing = [name for name in required if not _tex_file_found(kpsewhich, name)]
    if missing:
        hint = (
            "install texlive-fonts-extra"
            if any(name.startswith("newtx") for name in missing)
            else "install the TeX Live packages that provide them"
        )
        return DoctorCheck(LATEX_CHECK, False, f"TeX cannot find {', '.join(missing)}; {hint}")
    return DoctorCheck(LATEX_CHECK, True, f"latexmk and {len(required)} preamble file(s) are ready")


def _tex_file_found(kpsewhich: str, name: str) -> bool:
    result = subprocess.run(
        [kpsewhich, name], check=False, capture_output=True, text=True, timeout=15
    )
    return result.returncode == 0 and bool(result.stdout.strip())



def pdfinfo_check() -> DoctorCheck:
    """`pdfinfo` enforces the one-page resume budget in `resume_pdf.compile_pdf`."""
    return command_check("pdfinfo", "pdfinfo", ["-v"])


CHECKS = (latex_toolchain_check, pdfinfo_check)
