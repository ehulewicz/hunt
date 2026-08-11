"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import { type AnswerLibraryItem, getAnswers, saveAnswer } from "@/lib/jobs-api";

export function AnswersWorkspace() {
  const accessToken = useAccessToken();
  const [answers, setAnswers] = useState<AnswerLibraryItem[]>([]);
  const [key, setKey] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setAnswers(await getAnswers(accessToken)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Answers could not be loaded."); }
  }, [accessToken]);
  useEffect(() => {
    let active = true;
    void getAnswers(accessToken).then((loaded) => { if (active) setAnswers(loaded); }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Answers could not be loaded.");
    });
    return () => { active = false; };
  }, [accessToken]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await saveAnswer(accessToken, { key, normalized_question: question || null, answer_text: answer, answer_type: "text", approved });
      setKey(""); setQuestion(""); setAnswer(""); setApproved(false); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Answer could not be saved."); }
  }

  return (
    <div className="jobs-page">
      <header className="page-header"><p className="eyebrow">Jobs</p><h1>Answers</h1><p>Only approved reusable answers may be used by autofill. Claude drafts never approve themselves.</p></header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      <form className="form-section jobs-form" onSubmit={(event) => void submit(event)}>
        <h2>Add or replace an answer</h2><div className="form-grid">
          <label className="form-field"><span>Stable key</span><input onChange={(event) => setKey(event.target.value)} required value={key} /></label>
          <label className="form-field"><span>Normalized question</span><input onChange={(event) => setQuestion(event.target.value)} value={question} /></label>
          <label className="form-field form-wide"><span>Answer</span><textarea onChange={(event) => setAnswer(event.target.value)} required value={answer} /></label>
          <label className="check-control"><input checked={approved} onChange={(event) => setApproved(event.target.checked)} type="checkbox" />Approve for autofill</label>
        </div><button className="button button-primary" type="submit">Save answer</button>
      </form>
      <section className="jobs-section"><div className="section-heading"><div><h2>Answer library</h2><span>{answers.length} responses</span></div></div>{!answers.length ? <div className="panel-state">No reusable answers yet.</div> : null}<div className="jobs-list">{answers.map((item) => <article className="listing-row application-row" key={item.id}><div className="listing-main"><strong>{item.normalized_question ?? item.key}</strong><p>{item.answer_text}</p><div className="tag-list"><span className={item.approved ? "tag tag-success" : "tag"}>{item.approved ? "Approved" : "Draft"}</span></div></div></article>)}</div></section>
    </div>
  );
}
