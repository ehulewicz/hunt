"use client";

import { useEffect, useState } from "react";

import { loadHuntChanges, type Change, type CodeChangeDetail } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

export function ReplayResult({ check }: { check: Change | null | undefined }) {
  if (!check) return null;
  return <span>{check.sampled !== null && check.failed !== null
    ? `replay changed ${check.failed} of ${check.sampled} accepted answers`
    : check.status === "pending" ? "Replay pending" : check.status ? `Replay ${check.status}` : "Replay not recorded"}</span>;
}

function display(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "None";
}

export function ChangeList({ changes }: { changes: Change[] }) {
  if (changes.length === 0) return <p className="metadata-empty">No changes recorded.</p>;
  return <ol className="change-list">{[...changes].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((change) => {
    const code = change.subject === "code" && change.after && typeof change.after === "object" ? change.after as CodeChangeDetail : null;
    return <li key={change.id}>
      <p><time dateTime={change.created_at}>{new Date(change.created_at).toLocaleString()}</time> · {change.origin} · {change.action}</p>
      {code ? <>
        <strong>{code.summary ?? "Code fix"}</strong>
        <p><code>{code.commit ?? change.subject_id}</code> · {code.pushed === true ? "Pushed" : code.pushed === false ? "Not pushed" : "Push not recorded"}</p>
        {code.question && <p>{code.question}</p>}
        {code.cause && <p>Cause: {code.cause.replace(/_/g, " ")}</p>}
        {code.attempts !== undefined && <p>Attempts: {code.attempts}/3</p>}
        {code.files && <p>Files: {code.files.join(", ")}</p>}
        {code.probe !== undefined && <details><summary>Live probe</summary><pre>{display(code.probe)}</pre></details>}
        {code.diff && <details><summary>Diff</summary><pre>{code.diff}</pre></details>}
        <details><summary>Before → after</summary><pre>{display(change.before)}</pre><span>→</span><pre>{display(change.after)}</pre></details>
      </> : <div className="change-values"><span>Before</span><pre>{display(change.before)}</pre><span>→ After</span><pre>{display(change.after)}</pre></div>}
      <p className="muted">{change.status ?? "Verification not recorded"}{change.status && " · "}<ReplayResult check={change.status ? change : null} /></p>
      {change.verified_at && <p className="muted">Verified <time dateTime={change.verified_at}>{new Date(change.verified_at).toLocaleString()}</time></p>}
    </li>;
  })}</ol>;
}

// Keyed by subject and id by the caller, so another item's history never flashes here.
export function ItemHistory({ subject, subjectId }: { subject: Change["subject"]; subjectId?: string }) {
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const data = await loadHuntChanges(await freshAccessToken(), subject, subjectId);
        if (active) { setChanges(data); setError(null); }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Could not load history.");
      }
    }
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { active = false; clearInterval(timer); };
  }, [subject, subjectId, version]);
  const Heading = subject === "code" ? "h3" : "h4";
  return <section aria-label={subject === "code" ? "Code changes" : "History"} className="item-history">
    <Heading>{subject === "code" ? "Code changes" : "History"}</Heading>
    {error && <><p className="gap-error" role="alert">{error}{changes ? " Showing the last loaded changes." : ""}</p><button className="text-button" onClick={() => setVersion((value) => value + 1)} type="button">Retry history</button></>}
    {changes ? <ChangeList changes={changes} /> : !error && <p className="muted" role="status">Loading changes…</p>}
  </section>;
}
