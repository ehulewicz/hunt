"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  type AnswerLibraryItem,
  createAnswer,
  getAnswers,
  updateAnswer,
} from "@/lib/jobs-api";

export function AnswersWorkspace() {
  const accessToken = useAccessToken();
  const [answers, setAnswers] = useState<AnswerLibraryItem[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
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
      const payload = { question, answer_text: answer, approved };
      if (editingId) await updateAnswer(accessToken, editingId, payload);
      else await createAnswer(accessToken, payload);
      setEditingId(null); setQuestion(""); setAnswer(""); setApproved(false); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Answer could not be saved."); }
  }

  return (
    <div className="jobs-page">
      <header className="page-header"><p className="eyebrow">Jobs</p><h1>Answers</h1><p>Only approved reusable answers may be used by autofill. Claude drafts never approve themselves.</p></header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      <form className="form-section jobs-form" onSubmit={(event) => void submit(event)}>
        <h2>{editingId ? "Edit long-form answer" : "Add a long-form answer"}</h2><div className="form-grid">
          <label className="form-field form-wide"><span>Question</span><input onChange={(event) => setQuestion(event.target.value)} required value={question} /></label>
          <label className="form-field form-wide"><span>Answer</span><textarea onChange={(event) => setAnswer(event.target.value)} required value={answer} /></label>
          <label className="check-control"><input checked={approved} onChange={(event) => setApproved(event.target.checked)} type="checkbox" />Approve for autofill</label>
        </div><button className="button button-primary" type="submit">{editingId ? "Save changes" : "Save answer"}</button>
      </form>
      <section className="jobs-section"><div className="section-heading"><div><h2>Answer library</h2><span>{answers.length} responses</span></div></div>{!answers.length ? <div className="panel-state">No long-form answers yet.</div> : null}<div className="jobs-list">{answers.map((item) => <article className="listing-row application-row" key={item.id}><div className="listing-main"><strong>{item.question}</strong><p>{item.answer_text}</p><div className="tag-list"><span className={item.approved ? "tag tag-success" : "tag"}>{item.approved ? "Approved" : "Draft"}</span></div></div><div className="listing-actions"><button className="text-button" onClick={() => { setEditingId(item.id); setQuestion(item.question); setAnswer(item.answer_text); setApproved(item.approved); }} type="button">Edit</button></div></article>)}</div></section>
    </div>
  );
}
