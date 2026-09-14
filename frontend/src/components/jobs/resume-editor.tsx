"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createMasterResumeDraft,
  getDocumentPdf,
  updateMasterResumeDraft,
  type JobDocument,
} from "@/lib/jobs-api";

function formatJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function ResumeEditor({
  accessToken,
  source,
  onClose,
  onSaved,
}: {
  accessToken: string;
  source: JobDocument;
  onClose: () => void;
  onSaved: () => void;
}) {
  // A draft opened directly is its own working draft; an approved version seeds a new one.
  const [draftId, setDraftId] = useState<string | null>(
    source.status === "draft" ? source.id : null,
  );
  const [text, setText] = useState(() => formatJson(source.content_text ?? ""));
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pdfUrlRef = useRef<string | null>(null);

  const save = useCallback(async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (caught) {
      setError(caught instanceof Error ? `Invalid JSON: ${caught.message}` : "Invalid JSON.");
      return;
    }
    setError(null);
    try {
      const saved = draftId
        ? await updateMasterResumeDraft(accessToken, draftId, parsed)
        : await createMasterResumeDraft(accessToken, parsed);
      setDraftId(saved.id);
      setStatus(`Saved draft version ${saved.version}.`);
      onSaved();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Draft could not be saved.");
    }
  }, [accessToken, draftId, onSaved, text]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [save]);

  useEffect(() => {
    if (!source.storage_path) return;
    let active = true;
    void getDocumentPdf(accessToken, source.id)
      .then((blob) => {
        if (!active) return;
        const url = URL.createObjectURL(blob);
        pdfUrlRef.current = url;
        setPdfUrl(url);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [accessToken, source.id, source.storage_path]);

  useEffect(() => () => {
    if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
  }, []);

  return (
    <section className="resume-editor">
      <div className="section-heading-inline">
        <div>
          <h2>Main resume</h2>
          <p>
            {draftId
              ? "Editing a draft. Ctrl+S saves and recompiles; approve it to make it canonical."
              : "Ctrl+S creates a draft and compiles it. The approved version stays canonical until you approve."}
          </p>
        </div>
        <button className="text-button" onClick={onClose} type="button">Close</button>
      </div>
      {error ? <p className="notice notice-error">{error}</p> : null}
      {status && !error ? <p className="notice">{status}</p> : null}
      <div className="resume-editor-panes">
        <div className="resume-editor-pane">
          <label className="field-label" htmlFor="master-resume-json">Resume JSON</label>
          <textarea
            className="json-editor"
            id="master-resume-json"
            onChange={(event) => setText(event.target.value)}
            rows={30}
            spellCheck={false}
            value={text}
          />
        </div>
        <div className="resume-editor-pane">
          <div className="pane-heading">
            <span className="field-label">Compiled PDF</span>
          </div>
          {pdfUrl ? (
            <iframe className="document-preview" src={pdfUrl} title="Main resume preview" />
          ) : (
            <div className="panel-state">The PDF is compiled with `os hunt resume build`.</div>
          )}
        </div>
      </div>
      <div className="listing-actions">
        <button className="button button-primary" onClick={() => void save()} type="button">
          Save draft
        </button>
      </div>
    </section>
  );
}
