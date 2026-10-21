"use client";

import { ArrowLeft, Check } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

import { apiRequest, type Fact, type InboxQuestion, type Pattern, type Prose } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

export type Review = { kind: "fact"; item: Fact } | { kind: "question"; item: InboxQuestion }
  | { kind: "prose"; item: Prose } | { kind: "pattern"; item: Pattern };

export function valueText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
}

export function ReviewForm({ review, facts, onDone, onBack }: {
  review: Review; facts: Fact[]; onDone: () => void; onBack: () => void;
}) {
  const [text, setText] = useState(review.kind === "prose" ? review.item.text : review.kind === "fact" ? valueText(review.item.value) : "");
  const [factKey, setFactKey] = useState("");
  const [adding, setAdding] = useState(false);
  const [label, setLabel] = useState(review.kind === "question" ? review.item.question.sample_label : "");
  const [valueType, setValueType] = useState<Fact["value_type"]>("text");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const title = review.kind === "prose" ? review.item.question_label ?? "Written answer"
    : review.kind === "fact" ? review.item.label
      : review.kind === "question" ? review.item.question.sample_label
        : review.item.question_label ?? review.item.expression ?? "Suggested pattern";

  async function save(operation: (token: string) => Promise<unknown>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await operation(await freshAccessToken());
      onDone();
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
        method: "PATCH", body: JSON.stringify({ text, approved: true }),
      });
      if (review.kind === "fact") {
        const type = review.item.value_type;
        const value = ["boolean", "number", "list", "object"].includes(type) ? JSON.parse(text) : text;
        return apiRequest(`/v1/jobs/facts/${encodeURIComponent(review.item.key)}`, token, {
          method: "PUT", body: JSON.stringify({ value, verified: true }),
        });
      }
      if (review.kind === "pattern") return apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, {
        method: "PATCH", body: JSON.stringify({ status: "active" }),
      });
      if (adding) {
        const value = ["boolean", "number", "list", "object"].includes(valueType) ? JSON.parse(text) : text;
        await apiRequest(`/v1/jobs/facts/${encodeURIComponent(factKey)}`, token, {
          method: "PUT", body: JSON.stringify({ value, label, value_type: valueType, verified: true }),
        });
      }
      return apiRequest("/v1/jobs/patterns", token, {
        method: "POST", body: JSON.stringify({ kind: "binding", question_id: review.item.question.id, fact_key: factKey, status: "active", origin: "inbox" }),
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
          <p className="metadata-empty">Origin highlights are not available yet.</p>
          <label>Answer<textarea autoFocus onChange={(event) => setText(event.target.value)} required rows={8} value={text} /></label>
        </> : review.kind === "fact" ? <>
          <p className="muted">Confirm this fact before reusing it. An empty text value or [] means None. {review.item.value_type === "boolean" ? "Use true or false." : ["list", "object"].includes(review.item.value_type) ? "Enter valid JSON." : ""}</p>
          <label>Value<textarea autoFocus onChange={(event) => setText(event.target.value)} required={["boolean", "number", "list", "object"].includes(review.item.value_type)} rows={3} value={text} /></label>
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
          {review.item.formatter && <p className="muted">Format: {review.item.formatter}</p>}
        </>}
        {review.kind === "question" && selectedFact && !adding && <div className="fact-preview"><span>Current answer</span><strong>{factDisplayValue(selectedFact.value)}</strong></div>}
        {review.kind === "question" && review.item.answers.length > 0 && <details className="previous-answers"><summary>Previous answers</summary>{review.item.answers.map((answer, index) => <p key={index}><strong>{answer.company_name}</strong>: {answer.answer_text}</p>)}</details>}
        <div className="review-actions">
          <button className="button button-primary" disabled={busy || !text.trim() && (review.kind === "prose" || review.kind === "fact" && ["boolean", "number", "list", "object"].includes(review.item.value_type)) || review.kind === "pattern" && !selectedFact || review.kind === "question" && !factKey} type="submit">
            <Check aria-hidden="true" />{busy ? "Saving…" : review.kind === "prose" ? "Approve for reuse" : review.kind === "fact" ? "Save verified fact" : review.kind === "question" ? adding ? "Save fact and link" : "Link fact" : "Accept pattern"}
          </button>
          {review.kind === "pattern" && <button className="button button-secondary" onClick={() => void save((token) => apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ status: "rejected" }) }))} type="button">Reject</button>}
          {review.kind === "prose" && !review.item.approved && <button className="button button-secondary" disabled={!text.trim()} onClick={() => void save((token) => apiRequest(`/v1/jobs/prose/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ text, approved: false }) }))} type="button">Save draft</button>}
          {review.kind === "prose" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/prose/${review.item.id}`, token, { method: "DELETE" }))} type="button">{review.item.approved ? "Delete writing" : "Discard draft"}</button>}
          {review.kind === "question" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/questions/${review.item.question.id}`, token, { method: "PATCH", body: JSON.stringify({ disposition: "one_off" }) }))} type="button">Answer only on the form</button>}
        </div>
      </fieldset>
      {error && <p className="gap-error" role="alert">{error}</p>}
    </form>
  );
}

export function isNoneValue(value: unknown): boolean {
  return value === "" || Array.isArray(value) && value.length === 0;
}

export function factDisplayValue(value: unknown): string {
  return isNoneValue(value) ? "None (deliberate blank)" : valueText(value);
}
