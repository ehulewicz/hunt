"use client";

import { Check, FileSearch, FileText, Pencil } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  approveDocument,
  createMasterResumeDraft,
  getDocumentPdf,
  getDocumentReview,
  getDocuments,
  queueResumePreview,
  type JobDocument,
  type JobDocumentReview,
} from "@/lib/jobs-api";

export function DocumentsWorkspace({ documentType }: { documentType: "resume" | "cover_letter" }) {
  const accessToken = useAccessToken();
  const [documents, setDocuments] = useState<JobDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [review, setReview] = useState<JobDocumentReview | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [editing, setEditing] = useState<JobDocument | null>(null);
  const [draftText, setDraftText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const loaded = await getDocuments(accessToken);
      const filtered = loaded.filter((document) => documentType === "resume"
        ? document.document_type.includes("resume")
        : document.document_type === "cover_letter");
      setDocuments(filtered);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Documents could not be loaded.");
    }
  }, [accessToken, documentType]);

  useEffect(() => {
    let active = true;
    void getDocuments(accessToken).then((loaded) => {
      if (!active) return;
      setDocuments(loaded.filter((document) => documentType === "resume"
        ? document.document_type.includes("resume")
        : document.document_type === "cover_letter"));
    }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Documents could not be loaded.");
    });
    return () => { active = false; };
  }, [accessToken, documentType]);
  useEffect(() => () => {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
  }, [pdfUrl]);

  async function approve(document: JobDocument) {
    try {
      await approveDocument(accessToken, document.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Document could not be approved.");
    }
  }

  function edit(document: JobDocument) {
    setNotice(null);
    setError(null);
    let formatted = document.content_text ?? "";
    try {
      formatted = JSON.stringify(JSON.parse(formatted), null, 2);
    } catch {
      // Show the stored text as-is when it is not parseable rather than hiding it.
    }
    setDraftText(formatted);
    setEditing(document);
  }

  async function saveDraft() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draftText);
    } catch (caught) {
      setError(caught instanceof Error ? `Invalid JSON: ${caught.message}` : "Invalid JSON.");
      return;
    }
    try {
      const created = await createMasterResumeDraft(accessToken, parsed);
      setEditing(null);
      setNotice(`Saved draft version ${created.version}. Generate a preview before approving.`);
      setError(null);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Draft could not be saved.");
    }
  }

  async function preview(document: JobDocument) {
    try {
      await queueResumePreview(accessToken, document.id);
      setNotice("Preview queued. The paired worker compiles it and verifies it stays one page.");
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Preview could not be queued.");
    }
  }

  async function viewPdf(document: JobDocument) {
    try {
      const pdf = await getDocumentPdf(accessToken, document.id);
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
      setReview(null);
      setPdfUrl(URL.createObjectURL(pdf));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Resume PDF could not be loaded.");
    }
  }

  async function inspect(document: JobDocument) {
    try {
      const [loadedReview, pdf] = await Promise.all([
        getDocumentReview(accessToken, document.id),
        getDocumentPdf(accessToken, document.id),
      ]);
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
      setReview(loadedReview);
      setPdfUrl(URL.createObjectURL(pdf));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Resume review could not be loaded.");
    }
  }

  const title = documentType === "resume" ? "Resumes" : "Cover Letters";
  return (
    <div className="jobs-page">
      <header className="page-header">
        <p className="eyebrow">Jobs</p><h1>{title}</h1>
        <p>{documentType === "resume" ? "The approved main resume remains canonical. Tailored versions require diff review before autofill." : "Claude drafts remain application-specific and require approval."}</p>
      </header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}
      {editing ? (
        <section className="review-panel">
          <div className="section-heading-inline">
            <div>
              <h2>Main resume JSON</h2>
              <p>Editing version {editing.version}. Saving creates a new draft; the current approved version stays canonical until you approve.</p>
            </div>
            <button className="text-button" onClick={() => { setEditing(null); setError(null); }} type="button">Cancel</button>
          </div>
          <label className="field-label" htmlFor="master-resume-json">Resume document</label>
          <textarea
            className="json-editor"
            id="master-resume-json"
            onChange={(event) => setDraftText(event.target.value)}
            rows={24}
            spellCheck={false}
            value={draftText}
          />
          <div className="listing-actions">
            <button className="button button-primary" onClick={() => void saveDraft()} type="button">
              <Check aria-hidden="true" size={14} />Save draft
            </button>
          </div>
        </section>
      ) : null}
      <section className="jobs-section">
        <div className="section-heading"><div><h2>Document versions</h2><span>{documents.length} {title.toLowerCase()}</span></div></div>
        {!documents.length ? <div className="panel-state">No {title.toLowerCase()} are available yet.</div> : null}
        <div className="jobs-list">
          {documents.map((document) => (
            <article className="listing-row application-row" key={document.id}>
              <div className="listing-main"><strong>{document.title}</strong><p>Version {document.version} · {document.document_type.replaceAll("_", " ")}</p><div className="tag-list"><span className={document.status === "approved" ? "tag tag-success" : "tag"}>{document.status}</span>{document.is_current ? <span className="tag">Current</span> : null}</div></div>
              <div className="listing-actions">{document.document_type === "tailored_resume" ? <button className="button button-secondary" onClick={() => void inspect(document)}><FileSearch aria-hidden="true" size={14} />Review PDF &amp; changes</button> : null}{document.document_type === "master_resume" ? <button className="button button-secondary" onClick={() => edit(document)}><Pencil aria-hidden="true" size={14} />{document.status === "approved" ? "View & edit" : "Edit draft"}</button> : null}{document.document_type === "master_resume" && document.status === "draft" ? <button className="button button-secondary" onClick={() => void preview(document)}><FileText aria-hidden="true" size={14} />Generate preview</button> : null}{document.document_type === "master_resume" && document.storage_path ? <button className="button button-secondary" onClick={() => void viewPdf(document)}><FileSearch aria-hidden="true" size={14} />View PDF</button> : null}{document.status === "draft" ? <button className="button button-primary" onClick={() => void approve(document)}><Check aria-hidden="true" size={14} /> {document.document_type === "tailored_resume" ? "Approve & queue autofill" : "Approve exact version"}</button> : null}</div>
            </article>
          ))}
        </div>
      </section>
      {pdfUrl && !review ? (
        <section className="review-panel">
          <div className="section-heading-inline">
            <div><h2>Resume preview</h2><p>Generated by the paired worker and verified to be one page.</p></div>
            <button className="text-button" onClick={() => { URL.revokeObjectURL(pdfUrl); setPdfUrl(null); }} type="button">Close</button>
          </div>
          <iframe className="document-preview" src={pdfUrl} title="Main resume preview" />
        </section>
      ) : null}
      {review ? <section className="review-panel"><div className="section-heading-inline"><div><h2>Tailored resume review</h2><p>Source v{review.source_document.version} → tailored v{review.document.version} · {review.changed_word_ratio ? `${(Number(review.changed_word_ratio) * 100).toFixed(1)}% changed` : "Change ratio unavailable"}</p></div><button className="text-button" onClick={() => { if (pdfUrl) URL.revokeObjectURL(pdfUrl); setPdfUrl(null); setReview(null); }} type="button">Close</button></div>{pdfUrl ? <iframe className="document-preview" src={pdfUrl} title={`${review.document.title} PDF`} /> : null}<div className="review-details"><h3>Changed paths</h3><ul>{review.changed_paths.map((path) => <li key={path}><code>{path}</code></li>)}</ul><details><summary>JSON Patch</summary><pre>{JSON.stringify(review.resume_patch, null, 2)}</pre></details></div></section> : null}
    </div>
  );
}
