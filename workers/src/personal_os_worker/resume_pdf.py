from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def compile_resume(resume: dict[str, Any], output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    source = output_directory / "resume.tex"
    source.write_text(build_latex(resume), encoding="utf-8")
    process = subprocess.run(
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-outdir={output_directory}",
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf = output_directory / "resume.pdf"
    if process.returncode != 0 or not pdf.exists():
        raise RuntimeError((process.stdout + "\n" + process.stderr)[-2000:])
    page_check = subprocess.run(
        ["pdfinfo", str(pdf)], check=False, capture_output=True, text=True, timeout=15
    )
    pages = re.search(r"^Pages:\s+(\d+)$", page_check.stdout, re.MULTILINE)
    if page_check.returncode != 0 or pages is None:
        raise RuntimeError("Could not verify the generated resume page count")
    if int(pages.group(1)) != 1:
        raise RuntimeError(
            f"Tailored resume must remain one page; generated {pages.group(1)} pages"
        )
    return pdf


def build_latex(resume: dict[str, Any]) -> str:
    contact = resume.get("contact", {})
    education = resume.get("education", {})
    parts = [
        r"\documentclass[letterpaper,10pt]{article}",
        r"\usepackage[empty]{fullpage}",
        r"\usepackage[margin=0.48in]{geometry}",
        r"\usepackage{enumitem}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\usepackage{titlesec}",
        r"\pagenumbering{gobble}",
        r"\setlength{\parindent}{0pt}",
        r"\setlist[itemize]{leftmargin=14pt,itemsep=0pt,topsep=2pt,parsep=0pt}",
        r"\titleformat{\section}{\large\bfseries}{}{0pt}{}[\titlerule]",
        r"\titlespacing*{\section}{0pt}{5pt}{3pt}",
        r"\begin{document}",
        r"\begin{center}",
        rf"{{\Large\textbf{{{escape(str(contact.get('name', '')))}}}}}\\[-1pt]",
        " $\\vert$ ".join(
            escape(str(value))
            for value in (
                contact.get("phone", ""),
                contact.get("email", ""),
                contact.get("linkedin", ""),
                contact.get("citizenship", ""),
            )
            if value
        ),
        r"\end{center}",
        r"\section{Education}",
        (
            rf"\textbf{{{escape(str(education.get('school', '')))}}} \hfill "
            rf"{escape(str(education.get('graduation', '')))}\\"
        ),
        (
            rf"{escape(str(education.get('degree', '')))} \hfill GPA: "
            rf"{escape(str(education.get('gpa', '')))}"
        ),
    ]
    parts.extend(build_entries("Experience", resume.get("experience", [])))
    parts.extend(build_entries("Projects", resume.get("projects", [])))
    parts.append(r"\section{Skills}")
    for skill in resume.get("skills", []):
        parts.append(
            rf"\textbf{{{escape(str(skill.get('category', '')))}:}} "
            rf"{escape(str(skill.get('items', '')))}\\"
        )
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


def build_entries(title: str, entries: list[dict[str, Any]]) -> list[str]:
    lines = [rf"\section{{{title}}}"]
    for entry in entries:
        left = escape(str(entry.get("title", "")))
        org = escape(str(entry.get("org", "")))
        heading = f"{left} $\\vert$ {org}" if org else left
        lines.append(
            rf"\textbf{{{heading}}} \hfill {escape(str(entry.get('date', '')))}"
        )
        lines.append(r"\begin{itemize}")
        lines.extend(rf"\item {escape(str(bullet))}" for bullet in entry.get("bullets", []))
        lines.append(r"\end{itemize}")
    return lines


def escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "\u2013": "--",
        "\u2014": "---",
    }
    return "".join(replacements.get(character, character) for character in value)
