"""The resume commands: get, put, and the published PDF download."""

from __future__ import annotations

import argparse
import json

import httpx
import pytest

from personal_os_worker.cli import expand_shorthand, run_resume
from personal_os_worker.documents.resume import download_resume_pdf, rejection


class FakeApi:
    def __init__(self, document=None, pdf=b"%PDF-1.5", put_error=None):
        self.document = document or {"contact": {"name": "Alex Example"}}
        self.pdf = pdf
        self.put_error = put_error
        self.put_payload = None
        self.closed = False

    def career(self):
        return {"document": self.document}

    def put_career(self, document):
        if self.put_error:
            raise self.put_error
        self.put_payload = document
        return {"document": self.document}

    def resume_pdf(self):
        return self.pdf

    def close(self):
        self.closed = True


def status_error(status: int, detail) -> httpx.HTTPStatusError:
    request = httpx.Request("PUT", "https://api.test/v1/jobs/career")
    return httpx.HTTPStatusError(
        "refused", request=request,
        response=httpx.Response(status, json={"detail": detail}, request=request),
    )


def test_get_prints_the_document_as_json(tmp_path, capsys, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("personal_os_worker.cli.services.connected", lambda: (None, None))
    monkeypatch.setattr("personal_os_worker.cli.WorkerApi", lambda config, token: api)
    args = argparse.Namespace(resume_command="get")
    assert run_resume(args) == 0
    out = capsys.readouterr().out
    assert out == json.dumps(api.document, indent=2, ensure_ascii=False) + "\n"
    assert api.closed


def test_get_fails_when_the_response_has_no_document(monkeypatch):
    api = FakeApi()
    api.document = None
    monkeypatch.setattr("personal_os_worker.cli.services.connected", lambda: (None, None))
    monkeypatch.setattr("personal_os_worker.cli.WorkerApi", lambda config, token: api)
    with pytest.raises(RuntimeError, match="no document"):
        run_resume(argparse.Namespace(resume_command="get"))


def test_put_saves_the_file_and_reports_the_pdf(tmp_path, capsys, monkeypatch):
    api = FakeApi()
    source = tmp_path / "resume.json"
    source.write_text(json.dumps(api.document), encoding="utf-8")
    monkeypatch.setattr("personal_os_worker.cli.services.connected", lambda: (None, None))
    monkeypatch.setattr("personal_os_worker.cli.WorkerApi", lambda config, token: api)
    args = argparse.Namespace(resume_command="put", file=str(source))
    assert run_resume(args) == 0
    assert api.put_payload == api.document
    assert "Resume saved; /resume serves the new PDF." in capsys.readouterr().out


def test_put_refuses_invalid_json_with_line_and_column(tmp_path, capsys, monkeypatch):
    api = FakeApi()
    source = tmp_path / "bad.json"
    source.write_text('{"contact": }\n  broken', encoding="utf-8")
    monkeypatch.setattr("personal_os_worker.cli.services.connected", lambda: (None, None))
    monkeypatch.setattr("personal_os_worker.cli.WorkerApi", lambda config, token: api)
    assert run_resume(argparse.Namespace(resume_command="put", file=str(source))) == 1
    assert "line 1, column" in capsys.readouterr().out
    assert api.put_payload is None  # nothing was sent


def test_put_shows_the_api_refusal_and_exits_1(tmp_path, capsys, monkeypatch):
    api = FakeApi(put_error=status_error(422, "education must name exactly one primary school"))
    source = tmp_path / "resume.json"
    source.write_text(json.dumps(api.document), encoding="utf-8")
    monkeypatch.setattr("personal_os_worker.cli.services.connected", lambda: (None, None))
    monkeypatch.setattr("personal_os_worker.cli.WorkerApi", lambda config, token: api)
    assert run_resume(argparse.Namespace(resume_command="put", file=str(source))) == 1
    out = capsys.readouterr().out
    assert "Resume not saved: education must name exactly one primary school" in out


def test_download_writes_the_published_pdf_atomically(tmp_path):
    api = FakeApi()
    path = download_resume_pdf(api, tmp_path / "resume")
    assert path == tmp_path / "resume" / "Alex_Example.pdf"
    assert path.read_bytes() == b"%PDF-1.5"
    assert not (tmp_path / "resume" / ".Alex_Example.pdf.tmp").exists()


def test_rejection_formats_string_and_list_details():
    error = status_error(422, "a plain refusal")
    assert rejection(error) == "a plain refusal"
    error = status_error(422, [{"loc": ["body", "education"], "msg": "required"}])
    assert rejection(error) == "education: required"


def test_the_removed_resume_commands_and_shorthand_are_gone():
    assert expand_shorthand(["hunt", "resume"]) == ["hunt", "resume"]
