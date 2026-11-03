"use client";

import Link from "next/link";
import { ArrowLeft, BookOpen, Check, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { ItemHistory } from "@/components/hunt-history";
import { factDisplayValue, ReviewForm, type Review } from "@/components/hunt-review";
import { computedNote, loadHuntLibrary, type HuntLibrary, type Pattern, type Question } from "@/lib/api";
import { freshAccessToken } from "@/lib/device";

type Section = "Questions" | "Facts" | "Writing" | "Patterns" | "Code";
type Detail = { kind: "question"; item: Question } | { kind: "pattern"; item: Pattern };
const DISPOSITIONS: Record<Question["disposition"], string> = { unbound: "Not linked to a fact", pattern: "Linked to a pattern", one_off: "Answer only on the form", long_form: "Written answer" };
const PATTERN_STATUS: Record<string, string> = { active: "Active", candidate: "Suggested", repaired: "Repaired", validated: "Validated", rejected: "Rejected", retired: "Retired" };

export function HuntLibraryPanel({ onRefresh }: { onRefresh: () => void }) {
  const [section, setSection] = useState<Section>("Questions");
  const [library, setLibrary] = useState<HuntLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [review, setReview] = useState<Review | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    let active = true;
    let loading = false;
    async function load() {
      if (loading) return;
      loading = true;
      try {
        const data = await loadHuntLibrary(await freshAccessToken());
        if (active) { setLibrary(data); setError(null); }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Could not load your Library.");
      } finally {
        loading = false;
      }
    }
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { active = false; clearInterval(timer); };
  }, [version]);

  const counts = library ? { Questions: library.questions.length, Facts: library.facts.length + library.computed.length, Writing: library.prose.length, Patterns: library.patterns.length } : null;
  const query = search.trim().toLowerCase();
  const matches = (...values: (string | null)[]) => values.some((value) => value?.toLowerCase().includes(query));
  const questions = library?.questions.filter((item) => matches(item.sample_label, DISPOSITIONS[item.disposition])) ?? [];
  const facts = library?.facts.filter((item) => matches(item.label, item.key, factDisplayValue(item.value))) ?? [];
  const computed = library?.computed.filter((item) => matches(item.label, item.key, computedNote(item))) ?? [];
  const prose = library?.prose.filter((item) => matches(item.question_label, item.company_name, item.text)) ?? [];
  const patterns = library?.patterns.filter((item) => matches(item.question_label, item.expression, item.fact_key, PATTERN_STATUS[item.status])) ?? [];
  const visibleCount = { Questions: questions.length, Facts: facts.length + computed.length, Writing: prose.length, Patterns: patterns.length, Code: 0 }[section];
  const selectedPattern = detail?.kind === "pattern" ? detail.item : library?.patterns.find((item) => item.id === detail?.item.pattern_id);
  const selectedFact = library?.facts.find((item) => item.key === selectedPattern?.fact_key);
  const selectedComputed = library?.computed.find((item) => item.key === selectedPattern?.fact_key);

  return <>
    <div aria-label="Library categories" className="segment-control answer-categories" role="group">
      {(["Questions", "Facts", "Writing", "Patterns", "Code"] as const).map((name) => <button aria-pressed={section === name} key={name} onClick={() => { setSection(name); setReview(null); setDetail(null); setNotice(null); setSearch(""); }} type="button">{name}<span>{name === "Code" ? "" : counts?.[name] ?? "—"}</span></button>)}
    </div>
    {section !== "Code" && !review && !detail && <label className="library-search"><span className="sr-only">Search {section.toLowerCase()}</span><input onChange={(event) => setSearch(event.target.value)} placeholder={`Search ${section.toLowerCase()}`} type="search" value={search} /></label>}
    <div className="panel-scroll attention-content">
      {error && <div className="library-error"><p className="gap-error" role="alert">{error}{library ? " Showing the last loaded records." : ""}</p><button className="text-button" onClick={() => setVersion((current) => current + 1)} type="button">Try again</button></div>}
      {notice && <p className="saved-notice" role="status"><Check aria-hidden="true" />{notice}</p>}
      {section === "Code" ? <ItemHistory key="code" subject="code" /> : review ? <><ReviewForm allowDelete computed={library?.computed ?? []} facts={library?.facts ?? []} key={review.kind === "fact" || review.kind === "computed" ? review.item.key : review.kind === "question" ? review.item.question.id : review.item.id} onBack={() => setReview(null)} onDone={(action) => { setReview(null); setNotice(action === "deleted" ? "Deleted." : "Saved."); setVersion((current) => current + 1); onRefresh(); }} review={review} />{review.kind !== "computed" && <ItemHistory key={`${review.kind}:${review.kind === "fact" ? review.item.key : review.kind === "question" ? review.item.question.id : review.item.id}`} subject={review.kind} subjectId={review.kind === "fact" ? review.item.key ?? undefined : review.kind === "question" ? review.item.question.id : review.item.id} />}</>
        : detail ? <div className="review-form">
          <button className="text-button" onClick={() => setDetail(null)} type="button"><ArrowLeft aria-hidden="true" />All items</button>
          <h3>{detail.kind === "question" ? detail.item.sample_label : detail.item.question_label ?? detail.item.expression ?? "Pattern"}</h3>
          {detail.kind === "question" && <p>{DISPOSITIONS[detail.item.disposition]}</p>}
          {selectedPattern && <>
            <p>{PATTERN_STATUS[selectedPattern.status] ?? selectedPattern.status}</p>
            {selectedPattern.expression && <div className="fact-preview"><span>Expression</span><code>{selectedPattern.expression}</code></div>}
            <div className="fact-preview"><span>{selectedPattern.fact_key}</span><strong>{selectedFact ? factDisplayValue(selectedFact.value) : selectedComputed ? `${selectedComputed.label} — ${computedNote(selectedComputed)}` : "Fact not found"}</strong></div>
            {selectedPattern.formatter && <p className="muted">Format: {selectedPattern.formatter}</p>}
          </>}
          <ItemHistory key={`${detail.kind}:${detail.item.id}`} subject={detail.kind} subjectId={detail.item.id} />
        </div>
          : !library ? !error && <div className="panel-empty" role="status"><BookOpen aria-hidden="true" /><h3>Loading your Library</h3><p>Fetching saved questions, facts, writing, and patterns.</p></div>
            : <>
              {visibleCount === 0 ? <div className="panel-empty"><BookOpen aria-hidden="true" /><h3>{query ? "No matches" : `No ${section.toLowerCase()} saved`}</h3><p>{query ? "Try another search." : "Saved records from your applications will appear here."}</p></div>
                : <div className="review-list">
                  {section === "Questions" && questions.map((item) => <button aria-label={`View ${item.sample_label}`} className="review-row" key={item.id} onClick={() => setDetail({ kind: "question", item })} type="button"><span className="review-icon"><BookOpen aria-hidden="true" /></span><span><strong>{item.sample_label}</strong><small>{DISPOSITIONS[item.disposition]}</small></span><ChevronRight aria-hidden="true" /></button>)}
                  {section === "Facts" && facts.map((item) => <button aria-label={`Review ${item.label}`} className="review-row" key={item.key} onClick={() => setReview({ kind: "fact", item })} type="button"><span className="review-icon"><BookOpen aria-hidden="true" /></span><span><strong>{item.label}</strong><small>{factDisplayValue(item.value)}</small><small>{item.verified_at ? "Verified" : "Unverified"}</small></span><ChevronRight aria-hidden="true" /></button>)}
                  {section === "Facts" && computed.map((item) => <button aria-label={`Review ${item.label}`} className="review-row" key={item.key} onClick={() => setReview({ kind: "computed", item })} type="button"><span className="review-icon"><BookOpen aria-hidden="true" /></span><span><strong>{item.label}</strong><small>{computedNote(item)}</small></span><ChevronRight aria-hidden="true" /></button>)}
                  {section === "Writing" && prose.map((item) => <div className="review-row review-row-static" key={item.id}><span className="review-icon"><BookOpen aria-hidden="true" /></span><span><strong>{item.question_label ?? "Written answer"}</strong><small>{item.company_name ?? "Written answer"}: {item.approved ? "Approved" : "Old draft"}</small></span></div>)}
                  {section === "Patterns" && patterns.map((item) => <button aria-label={`View ${item.question_label ?? item.expression ?? "Pattern"}`} className="review-row" key={item.id} onClick={() => setDetail({ kind: "pattern", item })} type="button"><span className="review-icon"><BookOpen aria-hidden="true" /></span><span><strong>{item.question_label ?? item.expression ?? "Pattern"}</strong><small>{PATTERN_STATUS[item.status] ?? item.status}: {item.fact_key}</small></span><ChevronRight aria-hidden="true" /></button>)}
                </div>}
            </>}
    </div>
    <footer className="panel-footnote">{section === "Facts" ? <div>Stored facts can be deleted here. Computed facts change with their inputs: career facts in <Link href="/resume/edit">the resume editor</Link>, the rest through the facts they read.</div> : "Saved answers, patterns, and code changes."}</footer>
  </>;
}
