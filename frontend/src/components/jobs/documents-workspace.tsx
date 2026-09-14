"use client";

import { Check, FileSearch, Pencil } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ResumeEditor } from "@/components/jobs/resume-editor";
import { useAccessToken } from "@/components/workspace";
import {
  approveDocument,
  getDocumentPdf,
  getDocuments,
  type JobDocument,
} from "@/lib/jobs-api";

export function DocumentsWorkspace({ documentType }: { documentType: "resume" | "cover_letter" }) {
  const accessToken = useAccessToken();
  const [documents, setDocuments] = useState<JobDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [editing, setEditing] = useState<JobDocument | null>(null);
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
    setEditing(document);
  }

  async function viewPdf(document: JobDocument) {
    try {
      const pdf = await getDocumentPdf(accessToken, document.id);
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
      setPdfUrl(URL.createObjectURL(pdf));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Resume PDF could not be loaded.");
    }
  }

  const title = documentType === "resume" ? "Resumes" : "Cover Letters";
  return (
    <div className="jobs-page">
      <header className="page-header">
        <p className="eyebrow">Jobs</p><h1>{title}</h1>
        <p>{documentType === "resume" ? "The canonical resume lives in the repository; this is the stored history of web edits." : "Claude drafts remain application-specific and require approval."}</p>
      </header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}
      {editing ? (
        <ResumeEditor
          accessToken={accessToken}
          onClose={() => { setEditing(null); setError(null); }}
          onSaved={() => void load()}
          source={editing}
        />
      ) : null}
      <section className="jobs-section">
        <div className="section-heading"><div><h2>Document versions</h2><span>{documents.length} {title.toLowerCase()}</span></div></div>
        {!documents.length ? <div className="panel-state">No {title.toLowerCase()} are available yet.</div> : null}
        <div className="jobs-list">
          {documents.map((document) => (
            <article className="listing-row application-row" key={document.id}>
              <div className="listing-main"><strong>{document.title}</strong><p>Version {document.version} · {document.document_type.replaceAll("_", " ")}</p><div className="tag-list"><span className={document.status === "approved" ? "tag tag-success" : "tag"}>{document.status}</span>{document.is_current ? <span className="tag">Current</span> : null}</div></div>
              <div className="listing-actions">{document.document_type === "master_resume" ? <button className="button button-secondary" onClick={() => edit(document)}><Pencil aria-hidden="true" size={14} />{document.status === "approved" ? "View & edit" : "Edit draft"}</button> : null}{document.document_type === "master_resume" && document.storage_path ? <button className="button button-secondary" onClick={() => void viewPdf(document)}><FileSearch aria-hidden="true" size={14} />View PDF</button> : null}{document.status === "draft" ? <button className="button button-primary" onClick={() => void approve(document)}><Check aria-hidden="true" size={14} /> Approve exact version</button> : null}</div>
            </article>
          ))}
        </div>
      </section>
      {pdfUrl ? (
        <section className="review-panel">
          <div className="section-heading-inline">
            <div><h2>Resume preview</h2><p>The stored PDF for this version.</p></div>
            <button className="text-button" onClick={() => { URL.revokeObjectURL(pdfUrl); setPdfUrl(null); }} type="button">Close</button>
          </div>
          <iframe className="document-preview" src={pdfUrl} title="Main resume preview" />
        </section>
      ) : null}
    </div>
  );
}
