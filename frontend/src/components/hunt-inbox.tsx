"use client";

import { Check, CheckCheck, ChevronRight, FileText, Link2 } from "lucide-react";
import { useState } from "react";

import { HuntLibraryPanel } from "@/components/hunt-library";
import { factDisplayValue, isNoneValue, ReviewForm, type Review } from "@/components/hunt-review";
import type { Fact, Inbox } from "@/lib/api";

type Section = "Questions" | "Facts to verify" | "Writing" | "Pattern checks";

export function HuntInbox({ inbox, facts, onRefresh }: { inbox: Inbox | null; facts: Fact[]; onRefresh: () => void }) {
  const [view, setView] = useState<"Inbox" | "Library">("Inbox");
  const [section, setSection] = useState<Section>("Questions");
  const [review, setReview] = useState<Review | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const toVerify = facts.filter((fact) => !fact.verified_at || isNoneValue(fact.value));
  const counts = inbox ? { Questions: inbox.questions.length, "Facts to verify": toVerify.length, Writing: inbox.prose.length, "Pattern checks": inbox.candidates.length } : null;
  const total = counts ? Object.values(counts).reduce((sum, count) => sum + count, 0) : null;

  return (
    <section aria-label="Inbox and Library" className="dashboard-panel attention-panel">
      <header className="panel-header">
        <h2>{view}{view === "Inbox" && total !== null && total > 0 && <span className="count-badge">{total}</span>}</h2>
        <div aria-label="Answer workspace" className="segment-control compact" role="group">
          {(["Inbox", "Library"] as const).map((name) => <button aria-pressed={view === name} key={name} onClick={() => { setView(name); setReview(null); setNotice(null); }} type="button">{name}</button>)}
        </div>
      </header>
      {view === "Library" ? <HuntLibraryPanel onRefresh={onRefresh} /> : <>
        <div aria-label="Inbox categories" className="segment-control answer-categories" role="group">
          {(["Questions", "Facts to verify", "Writing", "Pattern checks"] as const).map((name) => <button aria-pressed={section === name} key={name} onClick={() => { setSection(name); setReview(null); setNotice(null); }} type="button">{name}<span>{counts?.[name] ?? "—"}</span></button>)}
        </div>
        <div className="panel-scroll attention-content">
          {notice && <p className="saved-notice" role="status"><Check aria-hidden="true" />{notice}</p>}
          {review ? <ReviewForm facts={facts} key={review.kind === "fact" ? review.item.key : review.kind === "question" ? review.item.question.id : review.item.id} onBack={() => setReview(null)} onDone={() => { setReview(null); setNotice("Saved."); onRefresh(); }} review={review} />
            : !inbox ? <div className="panel-empty"><FileText aria-hidden="true" /><h3>Your review inbox</h3><p>Questions, facts, writing, and pattern checks appear here when your overview loads.</p></div>
              : <>
                {section === "Questions" && <p className="metadata-empty category-note">Cause tags are not available yet.</p>}
                {section === "Pattern checks" && <p className="metadata-empty category-note">Verification results are not available yet. Suggested patterns can still be reviewed here.</p>}
                {counts?.[section] === 0 ? <div className="panel-empty"><CheckCheck aria-hidden="true" /><h3>{section === "Questions" ? "No questions to answer" : section === "Facts to verify" ? "No facts to verify" : section === "Writing" ? "No writing to review" : "No pattern checks available"}</h3><p>{section === "Questions" ? "Questions needing your input will appear here." : section === "Facts to verify" ? "Unverified facts and deliberate blanks stay visible here for review." : section === "Writing" ? "Written answers appear here before you approve them for reuse." : "Learned changes and their verification results will appear here when available."}</p></div>
                  : <div className="review-list">
                    {section === "Questions" && inbox.questions.map((item) => <button aria-label={`Review ${item.question.sample_label}`} className="review-row" key={item.question.id} onClick={() => setReview({ kind: "question", item })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{item.question.sample_label}</strong><small>{item.answers.length > 0 ? `${item.answers.length} previous answers` : "No previous answers"}</small></span><ChevronRight aria-hidden="true" /></button>)}
                    {section === "Facts to verify" && toVerify.map((item) => <button aria-label={`Review ${item.label}`} className="review-row" key={item.key} onClick={() => setReview({ kind: "fact", item })} type="button"><span className="review-icon"><Check aria-hidden="true" /></span><span><strong>{item.label}</strong><small>{factDisplayValue(item.value)}</small></span><ChevronRight aria-hidden="true" /></button>)}
                    {section === "Writing" && inbox.prose.map((item) => <button aria-label={`Review ${item.question_label ?? "Written answer"}`} className="review-row" key={item.id} onClick={() => setReview({ kind: "prose", item })} type="button"><span className="review-icon"><FileText aria-hidden="true" /></span><span><strong>{item.question_label ?? "Written answer"}</strong><small>{item.company_name ?? "Application answer"}</small></span><ChevronRight aria-hidden="true" /></button>)}
                    {section === "Pattern checks" && inbox.candidates.map(({ pattern }) => <button aria-label={`Review ${pattern.question_label ?? pattern.expression}`} className="review-row" key={pattern.id} onClick={() => setReview({ kind: "pattern", item: pattern })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{pattern.question_label ?? pattern.expression}</strong><small>Suggested pattern: {pattern.fact_key}</small></span><ChevronRight aria-hidden="true" /></button>)}
                  </div>}
              </>}
        </div>
        <footer className="panel-footnote">Review what your next application can reuse.</footer>
      </>}
    </section>
  );
}
