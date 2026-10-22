"use client";

import { ArrowLeft, Check } from "lucide-react";
import { type FormEvent, type ReactNode, useRef, useState } from "react";

import { ReplayResult } from "@/components/hunt-history";
import { apiRequest, deleteFact, type Change, type Fact, type InboxFact, type InboxQuestion, type Pattern, type Prose } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

export type Review = { kind: "fact"; item: Fact | InboxFact } | { kind: "question"; item: InboxQuestion }
  | { kind: "prose"; item: Prose } | { kind: "pattern"; item: Pattern; check?: Change | null };

export function valueText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
}

export function ReviewForm({ review: initialReview, facts, onDone, onBack, origin = "you", allowDelete = false }: {
  review: Review; facts: Fact[]; origin?: "you" | "inbox"; allowDelete?: boolean; onDone: (action?: "deleted") => void; onBack: () => void;
}) {
  // Missing cards carry the run's question, but may omit an existing fact's metadata.
  const storedFact = initialReview.kind === "fact" && "reason" in initialReview.item && initialReview.item.reason === "missing"
    ? facts.find((fact) => fact.key === initialReview.item.key) : undefined;
  const review: Review = initialReview.kind === "fact" && storedFact
    ? { ...initialReview, item: { ...initialReview.item, ...storedFact } } : initialReview;
  const [text, setText] = useState(review.kind === "prose" ? review.item.text : review.kind === "fact" ? "reason" in review.item && review.item.reason === "missing" && !storedFact ? "" : valueText(review.item.value) : "");
  const [factKey, setFactKey] = useState(review.kind === "fact" ? review.item.key ?? "" : "");
  const [editingProse, setEditingProse] = useState(false);
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState(review.kind === "question" ? review.item.question.sample_label : review.kind === "fact" ? review.item.label ?? ("missing_fact" in review.item ? review.item.missing_fact : null) ?? "" : "");
  const [valueType, setValueType] = useState<Fact["value_type"]>(review.kind === "fact" ? review.item.value_type ?? "text" : "text");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // /facts contains stored rows only. A matching key may be a removable
  // override of a career fact; computed-only facts have no row to delete.
  const canDelete = allowDelete && review.kind === "fact" && facts.some((fact) => fact.key === review.item.key);
  const inFlight = useRef(false);
  const title = review.kind === "prose" ? review.item.question_label ?? "Written answer"
    : review.kind === "fact" ? review.item.label ?? ("missing_fact" in review.item ? review.item.missing_fact : null) ?? "Missing fact"
      : review.kind === "question" ? review.item.question.sample_label
        : review.item.question_label ?? review.item.expression ?? "Suggested pattern";

  async function save(operation: (token: string) => Promise<unknown>, action?: "deleted") {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await operation(await freshAccessToken());
      onDone(action);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save. Try again.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void save(async (token) => {
      if (review.kind === "prose") return apiRequest(`/v1/jobs/prose/${review.item.id}`, token, {
        method: "PATCH", body: JSON.stringify({ text, approved: true, origin }),
      });
      if (review.kind === "fact") {
        const type = valueType;
        const value = ["boolean", "number", "list", "object"].includes(type) ? JSON.parse(text) : text;
        return apiRequest(`/v1/jobs/facts/${encodeURIComponent(factKey)}`, token, {
          method: "PUT", body: JSON.stringify({ value, verified: true, origin, ...(!review.item.key || !review.item.value_type ? { label, value_type: valueType } : {}) }),
        });
      }
      if (review.kind === "pattern") return apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, {
        method: "PATCH", body: JSON.stringify({ status: "active", origin }),
      });
      if (adding) {
        const value = ["boolean", "number", "list", "object"].includes(valueType) ? JSON.parse(text) : text;
        await apiRequest(`/v1/jobs/facts/${encodeURIComponent(factKey)}`, token, {
          method: "PUT", body: JSON.stringify({ value, label, value_type: valueType, verified: true, origin }),
        });
      }
      return apiRequest("/v1/jobs/patterns", token, {
        method: "POST", body: JSON.stringify({ kind: "binding", question_id: review.item.question.id, fact_key: factKey, status: "active", origin }),
      });
    });
  }

  const selectedFact = facts.find((fact) => fact.key === (review.kind === "pattern" ? review.item.fact_key : factKey));
  return (
    <form className="review-form" onSubmit={submit}>
      <button className="text-button" disabled={busy} onClick={onBack} type="button"><ArrowLeft aria-hidden="true" /> All items</button>
      <h3>{title}</h3>
      <fieldset disabled={busy}>
        {review.kind === "prose" ? <>
          <p className="muted">{review.item.company_name ?? "Application answer"}. Edit this answer, then approve it for future applications.</p>
          <p className="muted">{review.item.origin && `Cause: ${review.item.origin} · `}{review.item.submitted ? "Submitted answer" : "Not submitted"}</p>
          {review.item.provenance == null && <p className="metadata-empty">Origin highlights were not recorded for this answer.</p>}
          {review.item.provenance != null && !editingProse ? <>
            <p className="prose-legend"><span className="prose-model">Model</span> · <span className="prose-previous">Previous answer</span> · Your text is plain</p>
            <ProseOrigins item={review.item} />
            <button className="text-button" onClick={() => setEditingProse(true)} type="button">Edit answer</button>
          </> : <label>Answer<textarea autoFocus onChange={(event) => setText(event.target.value)} required rows={8} value={text} /></label>}
        </> : review.kind === "fact" ? <>
          <p className="muted">Confirm this fact before reusing it. An empty text value or [] means None. {valueType === "boolean" ? "Use true or false." : ["list", "object"].includes(valueType) ? "Enter valid JSON." : ""}</p>
          {"reason" in review.item && <>
            <p className="muted">Cause: {review.item.reason}</p>
            {review.item.question && <p>Question: {review.item.question}</p>}
            {review.item.missing_fact && <p>Needed: {review.item.missing_fact}</p>}
            <ReplayResult check={review.item.check} />
          </>}
          {!review.item.key && <label>Fact key<input onChange={(event) => setFactKey(event.target.value)} pattern="[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+" required value={factKey} /></label>}
          {(!review.item.key || !review.item.value_type) && <>
            <label>Label<input onChange={(event) => setLabel(event.target.value)} required value={label} /></label>
            <label>Type<select onChange={(event) => setValueType(event.target.value as Fact["value_type"])} value={valueType}>{["text", "boolean", "number", "date", "choice", "list", "object"].map((type) => <option key={type}>{type}</option>)}</select></label>
          </>}
          <label>Value<textarea autoFocus onChange={(event) => setText(event.target.value)} required={["boolean", "number", "list", "object"].includes(valueType)} rows={3} value={text} /></label>
        </> : review.kind === "question" ? <>
          <p className="muted">Choose the fact that answers this question. This link will apply to future forms.</p>
          <label>Fact
            <select onChange={(event) => { setFactKey(event.target.value); setAdding(false); }} required={!adding} value={adding ? "" : factKey}>
              <option value="">Choose a fact</option>
              {facts.map((fact) => <option key={fact.key} value={fact.key}>{fact.label}</option>)}
            </select>
          </label>
          <button className="text-button" onClick={() => { setAdding(!adding); setFactKey(""); }} type="button">{adding ? "Use an existing fact" : "Add a new fact"}</button>
          {adding && <>
            <label>Fact key<input onChange={(event) => setFactKey(event.target.value)} pattern="[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+" placeholder="work_auth.clearance" required value={factKey} /></label>
            <label>Label<input onChange={(event) => setLabel(event.target.value)} required value={label} /></label>
            <label>Type<select onChange={(event) => setValueType(event.target.value as Fact["value_type"])} value={valueType}>
              {["text", "boolean", "number", "date", "choice", "list", "object"].map((type) => <option key={type}>{type}</option>)}
            </select></label>
            <label>Value<input onChange={(event) => setText(event.target.value)} placeholder={valueType === "boolean" ? "true or false" : ""} required={["boolean", "number", "list", "object"].includes(valueType)} value={text} /></label>
          </>}
        </> : <>
          <p className="muted">This suggested pattern will answer matching questions using the fact below.</p>
          <div className="fact-preview"><span>{review.item.fact_key}</span><strong>{selectedFact ? factDisplayValue(selectedFact.value) : "Fact not found. Add it before accepting this pattern."}</strong></div>
          <ReplayResult check={review.check} />
          {review.item.origin && <p className="muted">Cause: {review.item.origin}</p>}
          {review.item.formatter && <p className="muted">Format: {review.item.formatter}</p>}
        </>}
        {review.kind === "question" && selectedFact && !adding && <div className="fact-preview"><span>Current answer</span><strong>{factDisplayValue(selectedFact.value)}</strong></div>}
        {review.kind === "question" && review.item.answers.length > 0 && <details className="previous-answers"><summary>Previous answers</summary>{review.item.answers.map((answer, index) => <p key={index}><strong>{answer.company_name}</strong>: {answer.answer_text}</p>)}</details>}
        <div className="review-actions">
          {confirmingDelete && review.kind === "fact" && canDelete ? <>
            <p role="alert">Delete {title}? This removes the stored fact. Any career-derived value with the same key will apply again.</p>
            <button className="button button-secondary" onClick={() => void save((token) => deleteFact(token, factKey), "deleted")} type="button">{busy ? "Deleting…" : "Confirm delete"}</button>
            <button className="text-button" onClick={() => { setConfirmingDelete(false); setError(null); }} type="button">Cancel</button>
          </> : <>
          <button className="button button-primary" disabled={busy || !text.trim() && (review.kind === "prose" || review.kind === "fact" && ["boolean", "number", "list", "object"].includes(valueType)) || review.kind === "pattern" && !selectedFact || review.kind === "question" && !factKey} type="submit">
            <Check aria-hidden="true" />{busy ? "Saving…" : review.kind === "prose" ? "Approve for reuse" : review.kind === "fact" ? "Save verified fact" : review.kind === "question" ? adding ? "Save fact and link" : "Link fact" : "Accept pattern"}
          </button>
          {review.kind === "pattern" && <button className="button button-secondary" onClick={() => void save((token) => apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ status: "rejected", origin }) }))} type="button">Reject</button>}
          {review.kind === "prose" && !review.item.approved && <button className="button button-secondary" disabled={!text.trim()} onClick={() => void save((token) => apiRequest(`/v1/jobs/prose/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ text, approved: false, origin }) }))} type="button">Save draft</button>}
          {review.kind === "prose" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/prose/${review.item.id}`, token, { method: "DELETE", body: JSON.stringify({ origin }) }))} type="button">{review.item.approved ? "Delete writing" : "Discard draft"}</button>}
          {review.kind === "question" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/questions/${review.item.question.id}`, token, { method: "PATCH", body: JSON.stringify({ disposition: "one_off", origin }) }))} type="button">Answer only on the form</button>}
          {canDelete && <button className="text-button" onClick={() => { setConfirmingDelete(true); setError(null); }} type="button">Delete fact</button>}
          </>}
        </div>
        {allowDelete && review.kind === "fact" && !canDelete && <p className="muted">Only stored facts can be deleted. Change career-derived facts with <code>os hunt resume edit</code>; other derived facts change in code.</p>}
      </fieldset>
      {error && <p className="gap-error" role="alert">{error}</p>}
    </form>
  );
}

export function isNoneValue(value: unknown): boolean {
  return value === "" || Array.isArray(value) && value.length === 0;
}

export function factDisplayValue(value: unknown): string {
  return isNoneValue(value) ? "None" : valueText(value);
}

// Backend offsets count Unicode code points, whereas String.slice counts UTF-16 units.
function ProseOrigins({ item }: { item: Prose }) {
  const characters = Array.from(item.text);
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const span of item.provenance ?? []) {
    parts.push(characters.slice(cursor, span.start).join(""));
    parts.push(<mark className={`prose-${span.source}`} key={span.start} title={span.source === "model" ? "Model" : "Previous answer"}>{characters.slice(span.start, span.end).join("")}</mark>);
    cursor = span.end;
  }
  parts.push(characters.slice(cursor).join(""));
  return <div aria-label="Answer origins" className="prose-origins">{parts}</div>;
}
