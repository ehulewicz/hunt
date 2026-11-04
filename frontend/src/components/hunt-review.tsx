"use client";

import Link from "next/link";
import { ArrowLeft, Check } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

import { ReplayResult } from "@/components/hunt-history";
import { apiRequest, readOnlyNote, deleteFact, type Change, type ReadOnlyFact, type Fact, type InboxFact, type InboxQuestion, type Pattern } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

export type Review = { kind: "fact"; item: Fact | InboxFact } | { kind: "question"; item: InboxQuestion }
  | { kind: "pattern"; item: Pattern; check?: Change | null } | { kind: "readOnly"; item: ReadOnlyFact };

export function valueText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
}

export function ReviewForm({ review: initialReview, readOnly = [], facts, onDone, onBack, origin = "you", allowDelete = false }: {
  review: Review; readOnly?: ReadOnlyFact[]; facts: Fact[]; origin?: "you" | "inbox"; allowDelete?: boolean; onDone: (action?: "deleted") => void; onBack: () => void;
}) {
  // Missing cards carry the run's question, but may omit an existing fact's metadata.
  const storedFact = initialReview.kind === "fact" && "reason" in initialReview.item && initialReview.item.reason === "missing"
    ? facts.find((fact) => fact.key === initialReview.item.key) : undefined;
  const review: Review = initialReview.kind === "fact" && storedFact
    ? { ...initialReview, item: { ...initialReview.item, ...storedFact } } : initialReview;
  const [text, setText] = useState(review.kind === "fact" ? "reason" in review.item && review.item.reason === "missing" && !storedFact ? "" : valueText(review.item.value) : "");
  const [factKey, setFactKey] = useState(review.kind === "fact" ? review.item.key ?? "" : "");
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState(review.kind === "question" ? review.item.question.sample_label : review.kind === "fact" ? review.item.label ?? ("missing_fact" in review.item ? review.item.missing_fact : null) ?? "" : "");
  const [valueType, setValueType] = useState<Fact["value_type"]>(review.kind === "fact" ? review.item.value_type ?? "text" : "text");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // /facts contains stored rows only. A stored row under a read-only key is a
  // removable leftover the worker ignores; record and computed facts have no row.
  const canDelete = allowDelete && review.kind === "fact" && facts.some((fact) => fact.key === review.item.key);
  const inFlight = useRef(false);
  const selectedFact = facts.find((fact) => fact.key === (review.kind === "pattern" ? review.item.fact_key : factKey));
  // The read-only fact behind the reviewed key, when there is one: never saved,
  // only named — its note and, for career facts, the resume editor.
  const selectedReadOnly = readOnly.find((item) => item.key === (review.kind === "readOnly" ? review.item.key : review.kind === "pattern" ? review.item.fact_key : review.kind === "fact" ? review.item.key : factKey));
  const typedReadOnly = review.kind === "question" && adding ? readOnly.find((item) => item.key === factKey) : undefined;
  const viewOnly = review.kind === "readOnly" || (review.kind === "fact" && selectedReadOnly !== undefined);
  const title = review.kind === "fact" ? review.item.label ?? ("missing_fact" in review.item ? review.item.missing_fact : null) ?? selectedReadOnly?.label ?? "Missing fact"
      : review.kind === "readOnly" ? review.item.label
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
      if (review.kind === "readOnly" || (review.kind === "fact" && selectedReadOnly)) return;
      if (review.kind === "fact") {
        const type = valueType;
        const value = ["boolean", "number", "list", "object"].includes(type) ? JSON.parse(text) : text;
        const answers = "issue_id" in review.item && review.item.issue_id ? { failure: { issue_id: review.item.issue_id } } : {};
        return apiRequest(`/v1/jobs/facts/${encodeURIComponent(factKey)}`, token, {
          method: "PUT", body: JSON.stringify({ value, verified: true, origin, ...(!review.item.key || !review.item.value_type ? { label, value_type: valueType } : {}), ...answers }),
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

  return (
    <form className="review-form" onSubmit={submit}>
      <button className="text-button" disabled={busy} onClick={onBack} type="button"><ArrowLeft aria-hidden="true" /> All items</button>
      <h3>{title}</h3>
      <fieldset disabled={busy}>
        {review.kind === "fact" ? <>
          {selectedReadOnly ? <>
            {"reason" in review.item && <>
              <p className="muted">Cause: {review.item.reason}</p>
              {review.item.question && <p>Question: {review.item.question}</p>}
              {review.item.missing_fact && <p>Needed: {review.item.missing_fact}</p>}
              <ReplayResult check={review.item.check} />
            </>}
            <p className="muted">{readOnlyNote(selectedReadOnly)}</p>
            {selectedReadOnly.source === "career" && <p><Link href="/resume/edit">Edit the career record</Link></p>}
          </> : <>
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
          </>}
        </> : review.kind === "readOnly" ? <>
          <p className="muted">{review.item.key}</p>
          <p className="muted">{readOnlyNote(review.item)}</p>
          {review.item.source === "career" && <p><Link href="/resume/edit">Edit the career record</Link></p>}
        </> : review.kind === "question" ? <>
          <p className="muted">Choose the fact that answers this question. This link will apply to future forms.</p>
          <label>Fact
            <select onChange={(event) => { setFactKey(event.target.value); setAdding(false); }} required={!adding} value={adding ? "" : factKey}>
              <option value="">Choose a fact</option>
              {facts.map((fact) => <option key={fact.key} value={fact.key}>{fact.label}</option>)}
              {readOnly.some((fact) => fact.kind === "static") && <optgroup label="From the career record">{readOnly.filter((fact) => fact.kind === "static").map((fact) => <option key={fact.key} value={fact.key}>{fact.label}</option>)}</optgroup>}
              {readOnly.some((fact) => fact.kind === "computed") && <optgroup label="Computed">{readOnly.filter((fact) => fact.kind === "computed").map((fact) => <option key={fact.key} value={fact.key}>{fact.label}</option>)}</optgroup>}
            </select>
          </label>
          <button className="text-button" onClick={() => { setAdding(!adding); setFactKey(""); }} type="button">{adding ? "Use an existing fact" : "Add a new fact"}</button>
          {adding && <>
            <label>Fact key<input onChange={(event) => setFactKey(event.target.value)} pattern="[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+" placeholder="work_auth.clearance" required value={factKey} /></label>
            {typedReadOnly && <p className="muted">{`${factKey} can't be saved here; choose it from the list`}</p>}
            <label>Label<input onChange={(event) => setLabel(event.target.value)} required value={label} /></label>
            <label>Type<select onChange={(event) => setValueType(event.target.value as Fact["value_type"])} value={valueType}>
              {["text", "boolean", "number", "date", "choice", "list", "object"].map((type) => <option key={type}>{type}</option>)}
            </select></label>
            <label>Value<input onChange={(event) => setText(event.target.value)} placeholder={valueType === "boolean" ? "true or false" : ""} required={["boolean", "number", "list", "object"].includes(valueType)} value={text} /></label>
          </>}
        </> : <>
          <p className="muted">This suggested pattern will answer matching questions using the fact below.</p>
          <div className="fact-preview"><span>{review.item.fact_key}</span><strong>{selectedFact ? factDisplayValue(selectedFact.value) : selectedReadOnly ? `${selectedReadOnly.label} — ${readOnlyNote(selectedReadOnly)}` : "Fact not found. Add it before accepting this pattern."}</strong></div>
          <ReplayResult check={review.check} />
          {review.item.origin && <p className="muted">Cause: {review.item.origin}</p>}
          {review.item.formatter && <p className="muted">Format: {review.item.formatter}</p>}
        </>}
        {review.kind === "question" && !adding && (selectedFact || selectedReadOnly) && <div className="fact-preview"><span>Current answer</span><strong>{selectedFact ? factDisplayValue(selectedFact.value) : selectedReadOnly ? readOnlyNote(selectedReadOnly) : ""}</strong></div>}
        {review.kind === "question" && review.item.answers.length > 0 && <details className="previous-answers"><summary>Previous answers</summary>{review.item.answers.map((answer, index) => <p key={index}><strong>{answer.company_name}</strong>: {answer.answer_text}</p>)}</details>}
        <div className="review-actions">
          {confirmingDelete && review.kind === "fact" && canDelete ? <>
            <p role="alert">Delete {title}? This removes the stored fact.</p>
            <button className="button button-secondary" onClick={() => void save((token) => deleteFact(token, factKey), "deleted")} type="button">{busy ? "Deleting…" : "Confirm delete"}</button>
            <button className="text-button" onClick={() => { setConfirmingDelete(false); setError(null); }} type="button">Cancel</button>
          </> : <>
          {!viewOnly && <button className="button button-primary" disabled={busy || !text.trim() && (review.kind === "fact" && ["boolean", "number", "list", "object"].includes(valueType)) || review.kind === "pattern" && !(selectedFact || selectedReadOnly) || review.kind === "question" && (!factKey || typedReadOnly !== undefined)} type="submit">
            <Check aria-hidden="true" />{busy ? "Saving…" : review.kind === "fact" ? "Save verified fact" : review.kind === "question" ? adding ? "Save fact and link" : "Link fact" : "Accept pattern"}
          </button>}
          {review.kind === "pattern" && <button className="button button-secondary" onClick={() => void save((token) => apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ status: "rejected", origin }) }))} type="button">Reject</button>}
          {review.kind === "question" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/questions/${review.item.question.id}`, token, { method: "PATCH", body: JSON.stringify({ disposition: "one_off", origin }) }))} type="button">Answer only on the form</button>}
          {canDelete && <button className="text-button" onClick={() => { setConfirmingDelete(true); setError(null); }} type="button">Delete fact</button>}
          </>}
        </div>
        {allowDelete && review.kind === "fact" && !canDelete && <p className="muted">Only stored facts can be deleted. Facts from the career record change in <Link href="/resume/edit">the resume editor</Link>; computed facts change with their inputs.</p>}
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
