"use client";

import { Check } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import { approveDocument, getDocuments, type JobDocument } from "@/lib/jobs-api";

export function DocumentsWorkspace({ documentType }: { documentType: "resume" | "cover_letter" }) {
  const accessToken = useAccessToken();
  const [documents, setDocuments] = useState<JobDocument[]>([]);
  const [error, setError] = useState<string | null>(null);

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

  async function approve(document: JobDocument) {
    try {
      await approveDocument(accessToken, document.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Document could not be approved.");
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
      <section className="jobs-section">
        <div className="section-heading"><div><h2>Document versions</h2><span>{documents.length} {title.toLowerCase()}</span></div></div>
        {!documents.length ? <div className="panel-state">No {title.toLowerCase()} are available yet.</div> : null}
        <div className="jobs-list">
          {documents.map((document) => (
            <article className="listing-row application-row" key={document.id}>
              <div className="listing-main"><strong>{document.title}</strong><p>Version {document.version} · {document.document_type.replaceAll("_", " ")}</p><div className="tag-list"><span className={document.status === "approved" ? "tag tag-success" : "tag"}>{document.status}</span>{document.is_current ? <span className="tag">Current</span> : null}</div></div>
              <div className="listing-actions">{document.status === "draft" ? <button className="button button-primary" onClick={() => void approve(document)}><Check aria-hidden="true" size={14} /> Approve exact version</button> : null}</div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
