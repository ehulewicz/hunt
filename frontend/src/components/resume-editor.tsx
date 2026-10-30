"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { DocumentEditor } from "@/components/document-editor";
import { loadCareer, saveCareer } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

export function ResumeEditor() {
  const [text, setText] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    freshAccessToken()
      .then((token) => loadCareer(token))
      .then((record) => { if (active) setText(JSON.stringify(record.document, null, 2)); })
      .catch((caught) => {
        if (active) setLoadError(caught instanceof Error ? caught.message : "Could not load the career record.");
      });
    return () => { active = false; };
  }, []);

  async function save(value: string): Promise<string> {
    let document: unknown;
    try {
      document = JSON.parse(value);
    } catch {
      throw new Error("The document is not valid JSON; fix it and save again.");
    }
    const record = await saveCareer(await freshAccessToken(), document);
    setText(JSON.stringify(record.document, null, 2));
    return "Saved. /resume now serves the new PDF.";
  }

  if (loadError) {
    return (
      <div className="centered-state"><strong>The career record did not load.</strong><span>{loadError}</span></div>
    );
  }
  if (text === null) {
    return <div aria-label="Loading the career record" className="centered-state"><span className="spinner" /></div>;
  }

  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Personal OS</p>
        <h1>Resume</h1>
        <p>
          The career record as its document; saving rebuilds the PDF.{" "}
          <Link href="/resume" target="_blank">Open /resume</Link>
        </p>
      </header>
      <p className="muted">
        Keep each entry&#39;s <code>id</code>; a new entry needs none. The contact line comes
        from your facts. <code>onResume</code> false keeps an entry, a school, or{" "}
        <code>moreBullets</code> off the printed resume.
      </p>
      <DocumentEditor
        claude={{
          surface: "resume",
          onAccept: (proposal) => { setText(proposal); return save(proposal).then(() => undefined); },
        }}
        label="Career record"
        monospace
        onChange={setText}
        onSave={save}
        saveLabel="Save record"
        value={text}
      />
    </>
  );
}
