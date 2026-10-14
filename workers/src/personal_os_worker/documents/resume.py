"""Resume commands over the career record.

The career record in the database is the resume. `artifacts/career/main.json`
and `Alex_Example.pdf` are its exports, regenerated whenever it changes and
committed for a portable, versioned copy; neither is read back as a source.
`resume edit` writes a working copy outside the repository, sends each save to
the API, and republishes the exports when the record accepts it.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from personal_os_worker.core.config import load_cli_config
from personal_os_worker.core.service import ensure_chrome, open_url, state_dir
from personal_os_worker.documents.resume_pdf import ENTRY_SECTIONS, compile_resume

if TYPE_CHECKING:
    from personal_os_worker.core.api import WorkerApi

DEFAULT_CDP_URL = "http://127.0.0.1:9333"


@dataclass(frozen=True, slots=True)
class ResumeBuild:
    source: Path
    pdf: Path
    tex: Path


def repository_root() -> Path:
    configured = os.environ.get("PERSONAL_OS_REPOSITORY")
    if configured:
        return Path(configured).expanduser().resolve()
    # workers/src/personal_os_worker/documents/resume.py -> repository root
    return Path(__file__).resolve().parents[4]


def canonical_resume_path() -> Path:
    """Where the committed JSON export of the career record lives."""
    configured = os.environ.get("PERSONAL_OS_RESUME_JSON")
    if configured:
        return Path(configured).expanduser().resolve()
    return repository_root() / "artifacts" / "career" / "main.json"


RESUME_PDF_NAME = "Alex_Example.pdf"


def default_pdf_path() -> Path:
    # The tracked PDF carries the applicant's name because ATS uploads keep it.
    return canonical_resume_path().with_name(RESUME_PDF_NAME)


def transcript_pdf_path() -> Path | None:
    """The tracked transcript beside the resume, when there is one."""
    path = repository_root() / "artifacts" / "career" / "Transcript.pdf"
    return path if path.exists() else None


def load_resume(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"Resume JSON not found: {path}") from None
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Resume JSON is invalid at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from None
    if not isinstance(payload, dict):
        raise RuntimeError("Resume JSON must contain one object")
    _validate_resume_shape(payload)
    return payload


def _validate_resume_shape(resume: dict[str, Any]) -> None:
    for section in ("contact", "education"):
        if not isinstance(resume.get(section), dict):
            raise RuntimeError(f"Resume section '{section}' must be an object")
    if not isinstance(resume.get("skills"), list):
        raise RuntimeError("Resume section 'skills' must be a list")
    # Entry sections are optional. A resume with research and no leadership and
    # one with leadership and no research are both well formed; only a section
    # that is present has to be shaped correctly.
    for section, _ in ENTRY_SECTIONS:
        if section in resume and not isinstance(resume[section], list):
            raise RuntimeError(f"Resume section '{section}' must be a list")
    for section, _ in ENTRY_SECTIONS:
        for index, entry in enumerate(resume.get(section, [])):
            if not isinstance(entry, dict):
                raise RuntimeError(f"Resume {section}[{index}] must be an object")
            bullets = entry.get("bullets")
            if (
                not isinstance(bullets, list)
                or not bullets
                or not all(isinstance(bullet, str) for bullet in bullets)
            ):
                raise RuntimeError(
                    f"Resume {section}[{index}].bullets must be a non-empty list of text"
                )
    for index, skill in enumerate(resume["skills"]):
        if not isinstance(skill, dict) or not all(
            isinstance(skill.get(key), str) and str(skill[key]).strip()
            for key in ("category", "items")
        ):
            raise RuntimeError(
                f"Resume skills[{index}] must contain non-empty category and items text"
            )


def build_resume(source: Path | None = None, output: Path | None = None) -> ResumeBuild:
    """Compile a resume document file to the PDF, without touching the record."""
    resolved_source = (source or canonical_resume_path()).expanduser().resolve()
    return build_document(load_resume(resolved_source), output, source=resolved_source)


def build_document(
    resume: dict[str, Any], output: Path | None = None, *, source: Path
) -> ResumeBuild:
    _validate_resume_shape(resume)
    resolved_output = (output or default_pdf_path()).expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # Compile away from the file a PDF viewer has open, then atomically replace
    # the stable target. A failed build therefore leaves the last good PDF intact.
    with tempfile.TemporaryDirectory(prefix="resume-build-", dir=resolved_output.parent) as raw:
        build_directory = Path(raw)
        compiled_pdf = compile_resume(resume, build_directory)
        compiled_tex = build_directory / "resume.tex"
        temporary_pdf = resolved_output.with_name(f".{resolved_output.name}.tmp")
        temporary_tex = resolved_output.with_suffix(".tex.tmp")
        shutil.copyfile(compiled_pdf, temporary_pdf)
        shutil.copyfile(compiled_tex, temporary_tex)
        temporary_pdf.replace(resolved_output)
        tex_output = resolved_output.with_suffix(".tex")
        temporary_tex.replace(tex_output)
    return ResumeBuild(source=source, pdf=resolved_output, tex=tex_output)


def document_text(document: dict[str, Any]) -> str:
    """The export's stable text: two-space JSON, authored characters kept, final newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_export(document: dict[str, Any], path: Path | None = None) -> Path:
    target = (path or canonical_resume_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(document_text(document), encoding="utf-8")
    temporary.replace(target)
    return target


def career_document(api: WorkerApi) -> dict[str, Any]:
    document = api.career().get("document")
    if not isinstance(document, dict):
        raise RuntimeError("The career record response carried no document")
    return document


def publish(document: dict[str, Any]) -> ResumeBuild:
    """Regenerate both exports, JSON then PDF, from a document the record accepted."""
    export = write_export(document)
    return build_document(document, source=export)


def publish_record(api: WorkerApi) -> ResumeBuild:
    return publish(career_document(api))


def import_resume(api: WorkerApi, source: Path | None = None) -> ResumeBuild:
    """Load a resume document into an empty career record, once, then republish."""
    resolved = (source or canonical_resume_path()).expanduser().resolve()
    record = api.import_career(load_resume(resolved))
    document = record.get("document")
    if not isinstance(document, dict):
        raise RuntimeError("The import response carried no document")
    return publish(document)


def ensure_resume(api: WorkerApi) -> ResumeBuild:
    """The tracked PDF as it is, publishing from the record only when it does not exist yet.

    Nothing tailors the resume any more, so the checked-in PDF is the one to
    attach; `os hunt resume build|edit` is where it gets regenerated.
    """
    source = canonical_resume_path()
    pdf = default_pdf_path()
    if pdf.exists():
        return ResumeBuild(source=source, pdf=pdf, tex=pdf.with_suffix(".tex"))
    return publish_record(api)


def rejection(error: httpx.HTTPStatusError) -> str:
    """What the API said was wrong with a document, as one line."""
    try:
        detail = error.response.json().get("detail")
    except ValueError:
        return error.response.text.strip() or str(error)
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg')}"
            for item in detail
            if isinstance(item, dict)
        )
    return str(detail)


def working_copy_path() -> Path:
    """Outside the repository, so the working copy is never mistaken for the export."""
    return state_dir() / "resume" / "resume.json"


class _PreviewServer:
    def __init__(self, pdf: Path) -> None:
        self.pdf = pdf
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.opened = False

    def __enter__(self) -> _PreviewServer:
        pdf = self.pdf

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                route = self.path.partition("?")[0]
                if route == "/resume.pdf":
                    if not pdf.exists():
                        self.send_error(404)
                        return
                    content = pdf.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                if route == "/version":
                    version = "missing"
                    if pdf.exists():
                        stat = pdf.stat()
                        version = f"{stat.st_mtime_ns}-{stat.st_size}"
                    content = version.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                if route != "/":
                    self.send_error(404)
                    return
                content = b"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Personal OS Resume</title>
<style>html,body,object{width:100%;height:100%;margin:0;background:#333}</style></head>
<body><object id=\"resume\" data=\"/resume.pdf\" type=\"application/pdf\"></object>
<script>
let version = "";
setInterval(async () => {
  const next = await fetch("/version", {cache:"no-store"}).then(r => r.text());
  if (version && next !== version) {
    document.getElementById("resume").data = "/resume.pdf?v=" + encodeURIComponent(next);
  }
  version = next;
}, 500);
</script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, _format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    @property
    def url(self) -> str:
        if self.server is None:
            raise RuntimeError("Resume preview server is not running")
        return f"http://127.0.0.1:{self.server.server_port}/"


def _is_wsl() -> bool:
    return platform.system() == "Linux" and "microsoft" in platform.release().casefold()


def open_live_preview(url: str) -> None:
    if not _is_wsl():
        if not webbrowser.open(url):
            raise RuntimeError(f"Could not open the resume preview. Open {url} manually.")
        return
    try:
        cdp_url = load_cli_config().cdp_url
    except RuntimeError:
        cdp_url = DEFAULT_CDP_URL
    ensure_chrome(cdp_url)
    open_url(cdp_url, url)


def edit_resume(
    api: WorkerApi,
    *,
    interval: float = 0.5,
    open_preview: bool = False,
) -> None:
    """Edit the career record as JSON: every save is validated by the API, then published."""
    if interval <= 0:
        raise RuntimeError("Watch interval must be greater than zero")
    working = working_copy_path()
    working.parent.mkdir(parents=True, exist_ok=True)
    working.write_text(document_text(career_document(api)), encoding="utf-8")
    output = default_pdf_path().expanduser().resolve()
    preview = _PreviewServer(output) if open_preview else None

    editor = shutil.which("code") or shutil.which("code.exe")
    if editor is None:
        raise RuntimeError("VS Code command 'code' was not found; install its shell command")
    subprocess.Popen(
        [editor, "--reuse-window", str(working)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"Editing {working}; every save updates the career record and its exports.")

    last_content = working.read_bytes()
    try:
        if preview is not None:
            preview.__enter__()
            if output.exists():
                open_live_preview(preview.url)
                preview.opened = True
                print(f"Live preview: {preview.url}")
        while True:
            time.sleep(interval)
            try:
                content = working.read_bytes()
            except FileNotFoundError:
                continue
            if content == last_content:
                continue
            last_content = content
            try:
                record = api.put_career(load_resume(working))
                result = publish(record["document"])
            except httpx.HTTPStatusError as error:
                print(f"Resume not updated: {rejection(error)}")
                continue
            except RuntimeError as error:
                print(f"Resume not updated: {error}")
                continue
            print(f"Resume updated: {result.pdf}")
            if preview is not None and not preview.opened:
                open_live_preview(preview.url)
                preview.opened = True
                print(f"Live preview: {preview.url}")
    except KeyboardInterrupt:
        print("Resume edit stopped.")
    finally:
        if preview is not None:
            preview.__exit__()
