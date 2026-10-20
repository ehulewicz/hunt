"use client";

import { ArrowLeft, Check, CheckCheck, ChevronRight, FileText, Link2, ListFilter } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

import { apiRequest, type Fact, type Inbox, type InboxQuestion, type Pattern, type Prose } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

type Review = { kind: "fact"; item: Fact } | { kind: "question"; item: InboxQuestion }
  | { kind: "prose"; item: Prose } | { kind: "pattern"; item: Pattern };
type Section = "Facts" | "Prose" | "Patterns";

function valueText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
}

function ReviewForm({ review, facts, onDone, onBack }: {
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
          <label>Answer<textarea autoFocus onChange={(event) => setText(event.target.value)} required rows={8} value={text} /></label>
        </> : review.kind === "fact" ? <>
          <p className="muted">Confirm this fact before reusing it. {review.item.value_type === "boolean" ? "Use true or false." : ["list", "object"].includes(review.item.value_type) ? "Enter valid JSON." : ""}</p>
          <label>Value<textarea autoFocus onChange={(event) => setText(event.target.value)} required rows={3} value={text} /></label>
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
            <label>Value<input onChange={(event) => setText(event.target.value)} placeholder={valueType === "boolean" ? "true or false" : ""} required value={text} /></label>
          </>}
        </> : <>
          <p className="muted">This suggested pattern will answer matching questions using the fact below.</p>
          <div className="fact-preview"><span>{review.item.fact_key}</span><strong>{selectedFact ? valueText(selectedFact.value) : "Fact not found. Add it before accepting this pattern."}</strong></div>
          {review.item.formatter && <p className="muted">Format: {review.item.formatter}</p>}
        </>}
        {review.kind === "question" && selectedFact && !adding && <div className="fact-preview"><span>Current answer</span><strong>{valueText(selectedFact.value)}</strong></div>}
        {review.kind === "question" && review.item.answers.length > 0 && <details className="previous-answers"><summary>Previous answers</summary>{review.item.answers.map((answer, index) => <p key={index}><strong>{answer.company_name}</strong>: {answer.answer_text}</p>)}</details>}
        <div className="review-actions">
          <button className="button button-primary" disabled={busy || !text.trim() && (review.kind === "fact" || review.kind === "prose") || review.kind === "pattern" && !selectedFact || review.kind === "question" && !factKey} type="submit">
            <Check aria-hidden="true" />{busy ? "Saving…" : review.kind === "prose" ? "Approve for reuse" : review.kind === "fact" ? "Save verified fact" : review.kind === "question" ? adding ? "Save fact and link" : "Link fact" : "Accept pattern"}
          </button>
          {review.kind === "pattern" && <button className="button button-secondary" onClick={() => void save((token) => apiRequest(`/v1/jobs/patterns/${review.item.id}`, token, { method: "PATCH", body: JSON.stringify({ status: "rejected" }) }))} type="button">Reject</button>}
          {review.kind === "prose" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/prose/${review.item.id}`, token, { method: "DELETE" }))} type="button">Discard draft</button>}
          {review.kind === "question" && <button className="text-button" onClick={() => void save((token) => apiRequest(`/v1/jobs/questions/${review.item.question.id}`, token, { method: "PATCH", body: JSON.stringify({ disposition: "one_off" }) }))} type="button">Answer only on the form</button>}
        </div>
      </fieldset>
      {error && <p className="gap-error" role="alert">{error}</p>}
    </form>
  );
}

export function HuntInbox({ inbox, facts, onRefresh }: { inbox: Inbox | null; facts: Fact[]; onRefresh: () => void }) {
  const [section, setSection] = useState<Section>("Facts");
  const [review, setReview] = useState<Review | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const unverified = facts.filter((fact) => !fact.verified_at);
  const counts = inbox ? { Facts: unverified.length + inbox.questions.length, Prose: inbox.prose.length, Patterns: inbox.candidates.length } : null;
  const total = counts ? counts.Facts + counts.Prose + counts.Patterns : null;

  return (
    <section aria-label="Needs attention" className="dashboard-panel attention-panel">
      <header className="panel-header"><div><h2>Needs attention{total !== null && total > 0 && <span className="count-badge">{total}</span>}</h2><p>A little input. Smoother applications.</p></div><ListFilter aria-hidden="true" /></header>
      <div aria-label="Review categories" className="segment-control">
        {(["Facts", "Prose", "Patterns"] as const).map((name) => <button aria-pressed={section === name} key={name} onClick={() => { setSection(name); setReview(null); setNotice(null); }} type="button">{name}<span>{counts?.[name] ?? "—"}</span></button>)}
      </div>
      <div className="panel-scroll attention-content">
        {notice && <p className="saved-notice" role="status"><Check aria-hidden="true" />{notice}</p>}
        {review ? <ReviewForm facts={facts} key={`${review.kind}:${review.kind === "fact" ? review.item.key : review.kind === "question" ? review.item.question.id : review.item.id}`} onBack={() => setReview(null)} onDone={() => { setReview(null); setNotice("Saved. Your reusable answers are up to date."); onRefresh(); }} review={review} />
          : !inbox ? <div className="panel-empty"><FileText aria-hidden="true" /><h3>Your review inbox</h3><p>Facts, written answers, and suggested patterns appear here when your overview loads.</p></div>
            : counts?.[section] === 0 ? <div className="panel-empty"><CheckCheck aria-hidden="true" /><h3>{section === "Facts" ? "Your facts are in good shape" : section === "Prose" ? "No writing to review" : "No patterns to review"}</h3><p>{section === "Facts" ? "New questions and facts to verify will appear here." : section === "Prose" ? "Written answers from your applications will appear here before you approve them for reuse." : "Suggested question-to-fact matches will appear here for your approval."}</p></div>
              : <div className="review-list">
                {section === "Facts" && <>
                  {inbox.questions.map((item) => <button aria-label={`Review ${item.question.sample_label}`} className="review-row" key={item.question.id} onClick={() => setReview({ kind: "question", item })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{item.question.sample_label}</strong><small>Needs an answer from your facts</small></span><ChevronRight aria-hidden="true" /></button>)}
                  {unverified.map((item) => <button aria-label={`Review ${item.label}`} className="review-row" key={item.key} onClick={() => setReview({ kind: "fact", item })} type="button"><span className="review-icon"><Check aria-hidden="true" /></span><span><strong>{item.label}</strong><small>Confirm this fact</small></span><ChevronRight aria-hidden="true" /></button>)}
                </>}
                {section === "Prose" && inbox.prose.map((item) => <button aria-label={`Review ${item.question_label ?? "Written answer"}`} className="review-row" key={item.id} onClick={() => setReview({ kind: "prose", item })} type="button"><span className="review-icon"><FileText aria-hidden="true" /></span><span><strong>{item.question_label ?? "Written answer"}</strong><small>{item.company_name ?? "Application answer"}</small></span><ChevronRight aria-hidden="true" /></button>)}
                {section === "Patterns" && inbox.candidates.map(({ pattern }) => <button aria-label={`Review ${pattern.question_label ?? pattern.expression}`} className="review-row" key={pattern.id} onClick={() => setReview({ kind: "pattern", item: pattern })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{pattern.question_label ?? pattern.expression}</strong><small>{pattern.fact_key}</small></span><ChevronRight aria-hidden="true" /></button>)}
              </div>}
      </div>
      <footer className="panel-footnote">Your approval decides what gets reused.</footer>
    </section>
  );
}
