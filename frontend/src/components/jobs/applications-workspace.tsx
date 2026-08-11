"use client";

import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import { getApplications, type JobApplication, updateApplication } from "@/lib/jobs-api";

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
                <strong>{application.title}</strong>
                <p>{application.company_name} · {application.location ?? "Location not listed"}</p>
                <div className="tag-list"><span className="tag">{application.status}</span></div>
              </div>
              <div className="listing-actions">
                {application.application_url ? <a href={application.application_url} rel="noreferrer" target="_blank">Company application <ExternalLink aria-hidden="true" size={13} /></a> : null}
                <select aria-label={`Status for ${application.title}`} onChange={(event) => void changeStatus(application, event.target.value)} value={application.status}>
                  <option value="preparing">Preparing</option><option value="ready">Ready</option><option value="submitted">Submitted</option><option value="interviewing">Interviewing</option><option value="offer">Offer</option><option value="rejected">Rejected</option><option value="withdrawn">Withdrawn</option><option value="archived">Archived</option>
                </select>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
