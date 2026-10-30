"use client";

import { BookOpen } from "lucide-react";
import { useEffect, useState } from "react";

import { DocumentEditor } from "@/components/document-editor";
import { freshAccessToken } from "@/lib/device";
import {
  loadLibrary,
  saveLibraryEntry,
  type LibraryEntry,
  type LibrarySection,
} from "@/lib/api";

const SECTIONS: [LibrarySection, string][] = [
  ["interest", "Interests"],
  ["direction", "Direction"],
  ["evidence", "Evidence"],
  ["story", "Story bank"],
  ["entry", "Experiences and projects"],
  ["note", "Technical notes"],
];

function wordCount(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean);
  return words.length;
}

export function LibraryEditor() {
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [unansweredOnly, setUnansweredOnly] = useState(false);

  useEffect(() => {
    let active = true;
    freshAccessToken()
      .then((token) => loadLibrary(token))
      .then((list) => { if (active) setEntries(list); })
      .catch((caught) => {
        if (active) setLoadError(caught instanceof Error ? caught.message : "Could not load the library.");
      });
    return () => { active = false; };
  }, []);

  function replace(updated: LibraryEntry) {
    setEntries((current) => current?.map((entry) => (entry.code === updated.code ? updated : entry)) ?? null);
  }

  if (loadError) {
    return <div className="centered-state"><strong>The library did not load.</strong><span>{loadError}</span></div>;
  }
  if (entries === null) {
    return <div aria-label="Loading the library" className="centered-state"><span className="spinner" /></div>;
  }
  if (entries.length === 0) {
    return (
      <div className="centered-state">
        <BookOpen aria-hidden="true" />
        <strong>The library is empty.</strong>
        <span>Import the question bank once with <code>os hunt library import &lt;file&gt;</code>.</span>
      </div>
    );
  }

  const visible = unansweredOnly ? entries.filter((entry) => !entry.answer?.trim()) : entries;

  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Personal OS</p>
        <h1>Library</h1>
        <p>Handwritten material that long-form answers are composed from.</p>
      </header>
      <label className="library-filter">
        <input
          checked={unansweredOnly}
          onChange={(event) => setUnansweredOnly(event.target.checked)}
          type="checkbox"
        />
        Unanswered only
      </label>
      {SECTIONS.map(([section, heading]) => {
        const group = visible.filter((entry) => entry.section === section);
        if (group.length === 0) return null;
        return (
          <section aria-label={heading} className="library-group" key={section}>
            <h2>{heading}</h2>
            {group.map((entry) => (
              <LibraryEntryEditor
                entry={entry}
                key={entry.code}
                onSaved={replace}
              />
            ))}
          </section>
        );
      })}
      {visible.length === 0 && <p className="muted">Every entry has an answer.</p>}
    </>
  );
}

function LibraryEntryEditor({
  entry,
  onSaved,
}: {
  entry: LibraryEntry;
  onSaved: (updated: LibraryEntry) => void;
}) {
  const [answer, setAnswer] = useState(entry.answer ?? "");
  const words = wordCount(answer);

  async function save(value: string): Promise<string> {
    const updated = await saveLibraryEntry(await freshAccessToken(), {
      code: entry.code,
      section: entry.section,
      entry_id: entry.entry_id,
      subject: entry.subject,
      prompt: entry.prompt,
      guidance: entry.guidance,
      answer: value.trim() ? value : null,
      display_order: entry.display_order,
    });
    onSaved(updated);
    setAnswer(updated.answer ?? "");
    return "Saved.";
  }

  return (
    <article className="library-entry">
      <header className="library-entry-head">
        <strong>{entry.code}</strong>
        {entry.subject && <span className="muted">{entry.subject}</span>}
      </header>
      <p>{entry.prompt}</p>
      {entry.guidance && (
        <details className="library-guidance">
          <summary>Guidance</summary>
          <p>{entry.guidance}</p>
        </details>
      )}
      <DocumentEditor
        claude={{
          surface: "library",
          disabledReason: answer.trim()
            ? undefined
            : "Write a first draft; Ask Claude only revises your words.",
          onAccept: (proposal) => { setAnswer(proposal); return save(proposal).then(() => undefined); },
        }}
        footer={<span className="muted">{words} {words === 1 ? "word" : "words"}</span>}
        label={`Answer ${entry.code}`}
        onChange={setAnswer}
        onSave={save}
        saveLabel="Save answer"
        value={answer}
      />
    </article>
  );
}
