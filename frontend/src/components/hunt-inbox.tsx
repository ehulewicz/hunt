"use client";

import Link from "next/link";
import { Check, CheckCheck, ChevronRight, FileText, Link2 } from "lucide-react";
import { useState } from "react";

import { ReplayResult } from "@/components/hunt-history";
import { HuntLibraryPanel } from "@/components/hunt-library";
import { factDisplayValue, ReviewForm, type Review } from "@/components/hunt-review";
import { readOnlyNote, loadLearned, retryFailure, retireCodeChange, retirePattern, type ReadOnlyFact, type Fact, type Inbox, type LearnedReport, type ProseGap, resolveProseGap } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

type Section = "Questions" | "Facts to verify" | "Writing" | "Pattern checks" | "Unsolved" | "Learned";

export function HuntInbox({ inbox, readOnly = [], facts, onRefresh }: { inbox: Inbox | null; readOnly?: ReadOnlyFact[]; facts: Fact[]; onRefresh: () => void }) {
  const [view, setView] = useState<"Inbox" | "Library">("Inbox");
  const [section, setSection] = useState<Section>("Questions");
  const [review, setReview] = useState<Review | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [learned, setLearned] = useState<LearnedReport | null>(null);
  const [learnedBusy, setLearnedBusy] = useState<string | null>(null);
  const toVerify = inbox?.facts ?? [];

  async function loadLearnedSection() {
    setSection("Learned");
    setReview(null);
    setNotice(null);
    if (learned) return;
    try {
      setLearned(await loadLearned(await freshAccessToken()));
    } catch {
      setLearned({ fixes: [], branches: [], unsolved: [], runs: [] });
      setNotice("Learned could not load.");
    }
  }

  async function learnedRefresh(message: string) {
    setLearned(await loadLearned(await freshAccessToken()));
    setNotice(message);
    onRefresh();
  }

  async function doRetireFix(id: string) {
    setLearnedBusy(id);
    try {
      await retireCodeChange(await freshAccessToken(), id);
      await learnedRefresh("Fix retired; the next run reverts it.");
    } catch { setNotice("Could not retire the fix."); }
    setLearnedBusy(null);
  }

  async function doRetireBranch(id: string) {
    setLearnedBusy(id);
    try {
      await retirePattern(await freshAccessToken(), id);
      await learnedRefresh("Branch retired.");
    } catch { setNotice("Could not retire the branch."); }
    setLearnedBusy(null);
  }

  async function doRetry(issueId: string) {
    setLearnedBusy(issueId);
    try {
      await retryFailure(await freshAccessToken(), issueId);
      await learnedRefresh("Retry scheduled: the next run tries it again.");
    } catch { setNotice("Could not retry it."); }
    setLearnedBusy(null);
  }

  async function resolveGap(gap: ProseGap) {
    try {
      await resolveProseGap(await freshAccessToken(), gap.id);
      setNotice("Resolved.");
      onRefresh();
    } catch {
      setNotice("Could not resolve the gap.");
    }
  }
  // Failed checks first: each would change an answer you accepted.
  const patterns = inbox ? [...(inbox.checks ?? []), ...inbox.candidates] : [];
  const counts: Record<string, number> | null = inbox ? { Questions: inbox.questions.length, "Facts to verify": toVerify.length, Writing: inbox.gaps?.length ?? 0, "Pattern checks": patterns.length, Unsolved: inbox.unsolved?.length ?? 0 } : null;
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
          {(["Questions", "Facts to verify", "Writing", "Pattern checks", "Unsolved"] as const).map((name) => <button aria-pressed={section === name} key={name} onClick={() => { setSection(name); setReview(null); setNotice(null); }} type="button">{name}<span>{counts?.[name] ?? "—"}</span></button>)}
          <button aria-pressed={section === "Learned"} key="Learned" onClick={() => void loadLearnedSection()} type="button">Learned<span>{learned ? learned.fixes.length + learned.branches.length : "—"}</span></button>
        </div>
        <div className="panel-scroll attention-content">
          {notice && <p className="saved-notice" role="status"><Check aria-hidden="true" />{notice}</p>}
          {review ? <ReviewForm readOnly={readOnly} origin="inbox" facts={facts} key={review.kind === "fact" || review.kind === "readOnly" ? review.item.key : review.kind === "question" ? review.item.question.id : review.item.id} onBack={() => setReview(null)} onDone={() => { setReview(null); setNotice("Saved."); onRefresh(); }} review={review} />
            : !inbox ? <div className="panel-empty"><FileText aria-hidden="true" /><h3>Your review inbox</h3><p>Questions, facts, writing, and pattern checks appear here when your overview loads.</p></div>
              : <>
                {section === "Learned" && !learned ? <div className="panel-empty"><FileText aria-hidden="true" /><h3>Loading</h3><p>The run&rsquo;s learned fixes, branches, problems and model usage are loading.</p></div>
                : counts?.[section] === 0 ? <div className="panel-empty"><CheckCheck aria-hidden="true" /><h3>{section === "Questions" ? "No questions to answer" : section === "Facts to verify" ? "No facts to verify" : section === "Writing" ? "No writing to review" : section === "Unsolved" ? "No unsolved issues" : "No pattern checks available"}</h3><p>{section === "Questions" ? "Questions needing your input will appear here." : section === "Facts to verify" ? "Facts needing verification, missing facts, and replay disagreements appear here." : section === "Writing" ? "Written answers and uncovered long-form questions appear here." : section === "Unsolved" ? "Issues the run could not fix will appear here." : "Learned changes and their verification results will appear here when available."}</p></div>
                  : <div className="review-list">
                    {section === "Questions" && inbox.questions.map((item) => <button aria-label={`Review ${item.question.sample_label}`} className="review-row" key={item.question.id} onClick={() => setReview({ kind: "question", item })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{item.question.sample_label}</strong><small>{item.answers.length > 0 ? `${item.answers.length} previous answers` : "No previous answers"}</small></span><ChevronRight aria-hidden="true" /></button>)}
                    {section === "Facts to verify" && toVerify.map((item) => {
                      const readOnlyItem = item.key ? readOnly.find((entry) => entry.key === item.key) : undefined;
                      return <button aria-label={`Review ${item.label ?? item.missing_fact ?? "Missing fact"}`} className="review-row" key={`${item.reason}:${item.issue_id ?? item.key}`} onClick={() => setReview({ kind: "fact", item })} type="button"><span className="review-icon"><Check aria-hidden="true" /></span><span><strong>{item.label ?? item.missing_fact ?? readOnlyItem?.label ?? "Missing fact"}</strong><small>Cause: {item.reason}</small>{item.question && <small>Question: {item.question}</small>}{item.missing_fact && <small>Needed: {item.missing_fact}</small>}{readOnlyItem && <small>{readOnlyNote(readOnlyItem)}</small>}{item.reason !== "missing" && <small>{factDisplayValue(item.value)}</small>}<small><ReplayResult check={item.check} /></small></span><ChevronRight aria-hidden="true" /></button>;
                    })}
                    {section === "Writing" && (inbox.gaps ?? []).map((gap) => <div className="review-row review-row-static" key={gap.id}>
                      <span className="review-icon"><FileText aria-hidden="true" /></span>
                      <span><strong>{gap.question ?? "Long-form question"}</strong>
                        <small>{[gap.company_name, gap.title].filter(Boolean).join(" · ") || "Any application"}</small>
                        <small>{gap.coverage === "missing" ? "Not covered" : "Partly covered"} · {gap.family.replace(/_/g, " ")}</small>
                        {gap.missing_detail && <small>{gap.missing_detail}</small>}
                      </span>
                      <span className="review-row-actions">
                        <Link className="text-button" href="/library">Write in Library</Link>
                        <button className="button button-secondary" onClick={() => void resolveGap(gap)} type="button">Resolve</button>
                      </span>
                    </div>)}
                    {section === "Pattern checks" && patterns.map(({ pattern, check }) => <button aria-label={`Review ${pattern.question_label ?? pattern.expression}`} className="review-row" key={pattern.id} onClick={() => setReview({ kind: "pattern", item: pattern, check })} type="button"><span className="review-icon"><Link2 aria-hidden="true" /></span><span><strong>{pattern.question_label ?? pattern.expression}</strong><small>{check ? <ReplayResult check={check} /> : `Suggested pattern: ${pattern.fact_key}`}</small>{(check || pattern.origin) && <small>Cause: {check ? "replay disagreement" : pattern.origin}</small>}</span><ChevronRight aria-hidden="true" /></button>)}
                    {section === "Learned" && learned && (
                      <div className="learned-lists">
                        <h3>Fixes</h3>
                        {learned.fixes.length === 0 && <p className="dim">No learned code yet.</p>}
                        {learned.fixes.map((fix) => <article className="unsolved-row" key={fix.change_id}>
                          <span><strong>{fix.file}</strong><span className={`pill pill-${fix.status}`}>{fix.status}</span></span>
                          <small>{fix.cause?.replace(/_/g, " ")} · {fix.question}</small>
                          {fix.summary && <small>{fix.summary}</small>}
                          <small><time dateTime={fix.created_at}>{new Date(fix.created_at).toLocaleString()}</time></small>
                          {fix.status !== "retired" && <span className="review-row-actions"><button className="button button-secondary" disabled={learnedBusy === fix.change_id} onClick={() => void doRetireFix(fix.change_id)} type="button">Revert</button></span>}
                        </article>)}
                        <h3>Branches</h3>
                        {learned.branches.length === 0 && <p className="dim">No model-taught branches.</p>}
                        {learned.branches.map((branch) => <article className="unsolved-row" key={branch.pattern_id ?? branch.fact_key ?? branch.question}>
                          <strong>{branch.expression ?? branch.question}</strong>
                          <small>{branch.fact_key}</small>
                          <small><time dateTime={branch.created_at}>{new Date(branch.created_at).toLocaleString()}</time></small>
                          {branch.pattern_id && <span className="review-row-actions"><button className="button button-secondary" disabled={learnedBusy === branch.pattern_id} onClick={() => void doRetireBranch(branch.pattern_id!)} type="button">Revert</button></span>}
                        </article>)}
                        <h3>Unsolved</h3>
                        {learned.unsolved.length === 0 && <p className="dim">No problems waiting for Retry.</p>}
                        {learned.unsolved.map((item) => <article className="unsolved-row" key={item.issue_id}>
                          <strong>{item.label ?? item.cause.replace(/_/g, " ")}</strong>
                          <small>{item.cause.replace(/_/g, " ")}{item.site ? ` · ${item.site}` : ""}</small>
                          {item.attempts && <small>{item.attempts}</small>}
                          <span className="review-row-actions"><button className="button button-secondary" disabled={learnedBusy === item.issue_id} onClick={() => void doRetry(item.issue_id)} type="button">Retry</button></span>
                        </article>)}
                        <h3>Model usage</h3>
                        {learned.runs.length === 0 && <p className="dim">No runs yet.</p>}
                        {learned.runs.map((run) => <article className="unsolved-row" key={run.run_id}>
                          <strong>{run.site ?? "Application"}</strong>
                          <small><time dateTime={run.started_at}>{new Date(run.started_at).toLocaleString()}</time></small>
                          <small>{(["pattern", "code", "audit", "draft"] as const).map((lane) => {
                            const calls = run.model_usage?.[lane]?.calls ?? 0;
                            const tokens = (run.model_usage?.[lane]?.input_tokens ?? 0) + (run.model_usage?.[lane]?.output_tokens ?? 0);
                            return `${lane} ${calls}${tokens ? ` (${tokens} tokens)` : ""}`;
                          }).join(" · ")}</small>
                          <small>learning {(run.model_usage?.pattern?.calls ?? 0) + (run.model_usage?.code?.calls ?? 0)}</small>
                        </article>)}
                      </div>
                    )}
                    {section === "Unsolved" && inbox.unsolved.map((item) => <article className="unsolved-row" key={item.issue_id}>
                      <strong>{item.question ?? item.control_kind ?? "Application issue"}</strong>
                      <small>Cause: {item.category.replace(/_/g, " ")}{item.control_kind ? ` · ${item.control_kind}` : ""}</small>
                      {item.note && <p>{item.note}</p>}
                      <small>{item.occurrence_count} occurrences · Last seen <time dateTime={item.last_seen_at}>{new Date(item.last_seen_at).toLocaleString()}</time></small>
                    </article>)}
                  </div>}
              </>}
        </div>
        <footer className="panel-footnote">Review what your next application can reuse.</footer>
      </>}
    </section>
  );
}
