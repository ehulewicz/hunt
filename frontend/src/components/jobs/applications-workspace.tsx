"use client";

import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  APPLICATION_STATUSES,
  deleteApplication,
  getApplications,
  type JobApplication,
  updateApplication,
} from "@/lib/jobs-api";

export function ApplicationsWorkspace() {
  const accessToken = useAccessToken();
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await getApplications(accessToken);
      setApplications(Array.isArray(loaded) ? loaded : []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Applications could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    let active = true;
    void getApplications(accessToken).then((loaded) => {
      if (active) setApplications(Array.isArray(loaded) ? loaded : []);
    }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Applications could not be loaded.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [accessToken]);

  async function changeStatus(application: JobApplication, status: string) {
    try {
      await updateApplication(accessToken, application.id, { status });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Application could not be updated.");
    }
  }

  async function remove(application: JobApplication) {
    if (!window.confirm(`Delete ${application.title} at ${application.company_name}? You can select the listing again later.`)) return;
    try {
      await deleteApplication(accessToken, application.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Application could not be deleted.");
    }
  }

  return (
    <div className="jobs-page">
      <header className="page-header">
        <p className="eyebrow">Jobs</p><h1>Applications</h1>
        <p>Preparation is automated; submission status is always confirmed manually.</p>
      </header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      <section className="jobs-section">
        <div className="section-heading"><div><h2>Application pipeline</h2><span>{applications.length} applications</span></div></div>
        {loading ? <div className="panel-state"><span className="spinner" /> Loading applications…</div> : null}
        {!loading && !applications.length ? <div className="panel-state">Select listings from the daily inbox to begin.</div> : null}
        <div className="jobs-list">
          {applications.map((application) => (
            <article className="listing-row application-row" key={application.id}>
              <div className="listing-main">
                {application.application_url ? <a className="listing-title-link" href={application.application_url} rel="noreferrer" target="_blank">{application.title}</a> : <strong>{application.title}</strong>}
                <p>{application.company_name} · {application.location ?? "Location not listed"}</p>
                <div className="tag-list"><span className={application.status === "applied" ? "tag tag-success" : "tag"}>{application.status.replaceAll("_", " ")}</span>{application.applied_at ? <span className="tag">Applied {application.applied_at.slice(0, 10)}</span> : null}</div>
                {application.unanswered_required_questions.length ? <p className="task-warning">Needs answers: {application.unanswered_required_questions.join(" · ")}</p> : null}
                {application.next_action ? <p>{application.next_action}</p> : null}
              </div>
              <div className="listing-actions">
                <select aria-label={`Status for ${application.title}`} onChange={(event) => void changeStatus(application, event.target.value)} value={application.status}>
                  {APPLICATION_STATUSES.map((status) => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}
                </select>
                <button className="text-button danger-action" onClick={() => void remove(application)} type="button"><Trash2 aria-hidden="true" size={12} />Delete</button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
