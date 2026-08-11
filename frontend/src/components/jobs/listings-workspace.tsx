"use client";

import { BriefcaseBusiness, ExternalLink, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  batchPrepare,
  type CareerStage,
  getListings,
  type JobListing,
  type RoleFamily,
  syncListings,
} from "@/lib/jobs-api";

const roleOptions: { value: RoleFamily | "all"; label: string }[] = [
  { value: "all", label: "All roles" },
  { value: "software_engineering", label: "Software" },
  { value: "data_engineering", label: "Data engineering" },
  { value: "ml_engineering", label: "ML engineering" },
  { value: "data_science", label: "Data science" },
  { value: "other", label: "Other technical" },
];

const roleLabels: Record<RoleFamily, string> = {
  software_engineering: "Software",
  data_engineering: "Data engineering",
  ml_engineering: "ML engineering",
  data_science: "Data science",
  other: "Other",
};

export function ListingsWorkspace() {
  const accessToken = useAccessToken();
  const [items, setItems] = useState<JobListing[]>([]);
  const [total, setTotal] = useState(0);
  const [careerStage, setCareerStage] = useState<CareerStage | "all">("internship");
  const [roleFamily, setRoleFamily] = useState<RoleFamily | "all">("all");
  const [freshOnly, setFreshOnly] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getListings(accessToken, {
        careerStage,
        roleFamily,
        freshOnly,
        query,
      });
      setItems(Array.isArray(page.items) ? page.items : []);
      setTotal(typeof page.total === "number" ? page.total : 0);
      setSelected(new Set());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Listings could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [accessToken, careerStage, freshOnly, query, roleFamily]);

  useEffect(() => {
    let active = true;
    void getListings(accessToken, { careerStage, roleFamily, freshOnly, query })
      .then((page) => {
        if (!active) return;
        setItems(Array.isArray(page.items) ? page.items : []);
        setTotal(typeof page.total === "number" ? page.total : 0);
        setSelected(new Set());
        setError(null);
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : "Listings could not be loaded.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [accessToken, careerStage, freshOnly, query, roleFamily]);

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      const result = await syncListings(accessToken);
      setNotice(`Synced ${result.open} open company listings from SpeedyApply.`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "GitHub sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  async function handlePrepare() {
    if (!selected.size) return;
    setPreparing(true);
    setError(null);
    try {
      const result = await batchPrepare(accessToken, [...selected]);
      setNotice(`Queued ${result.application_ids.length} application preparations.`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Applications could not be queued.");
    } finally {
      setPreparing(false);
    }
  }

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="jobs-page">
      <header className="page-header jobs-page-header">
        <div>
          <p className="eyebrow">Jobs</p>
          <h1>Job Listings</h1>
          <p>
            Every SpeedyApply SWE posting is retained. Role labels help you narrow the list;
            they never remove unmatched opportunities.
          </p>
        </div>
        <button className="button button-secondary" disabled={syncing} onClick={handleSync}>
          <RefreshCw aria-hidden="true" size={15} />
          {syncing ? "Syncing…" : "Sync GitHub"}
        </button>
      </header>

      <section aria-label="Listing filters" className="jobs-toolbar">
        <label>
          <span>Stage</span>
          <select
            aria-label="Career stage"
            onChange={(event) => setCareerStage(event.target.value as CareerStage | "all")}
            value={careerStage}
          >
            <option value="internship">Internships</option>
            <option value="new_grad">New grad</option>
            <option value="all">Both stages</option>
          </select>
        </label>
        <label>
          <span>Role family</span>
          <select
            aria-label="Role family"
            onChange={(event) => setRoleFamily(event.target.value as RoleFamily | "all")}
            value={roleFamily}
          >
            {roleOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="jobs-search">
          <span>Search</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Company, title, or location"
            type="search"
            value={query}
          />
        </label>
        <label className="check-control">
          <input
            checked={freshOnly}
            onChange={(event) => setFreshOnly(event.target.checked)}
            type="checkbox"
          />
          <span>New in the last 48 hours</span>
        </label>
      </section>

      <div aria-live="polite" className="jobs-feedback">
        {error ? <p className="notice notice-error">{error}</p> : null}
        {notice ? <p className="notice notice-success">{notice}</p> : null}
      </div>

      <section className="jobs-section">
        <div className="section-heading">
          <div><h2>{freshOnly ? "Daily inbox" : "Listing backlog"}</h2><span>{total} matches</span></div>
          <button
            className="button button-primary"
            disabled={!selected.size || preparing}
            onClick={handlePrepare}
          >
            <BriefcaseBusiness aria-hidden="true" size={15} />
            {preparing ? "Queueing…" : `Prepare selected (${selected.size})`}
          </button>
        </div>

        {loading ? <div className="panel-state"><span className="spinner" /> Loading listings…</div> : null}
        {!loading && !items.length ? (
          <div className="panel-state">No listings match these filters. Sync GitHub or open the backlog.</div>
        ) : null}
        {!loading && items.length ? (
          <div className="jobs-list">
            {items.map((listing) => (
              <article className="listing-row" key={listing.id}>
                <label className="listing-select">
                  <input
                    aria-label={`Select ${listing.title} at ${listing.company_name}`}
                    checked={selected.has(listing.id)}
                    disabled={Boolean(listing.application_id) || !listing.application_url}
                    onChange={() => toggle(listing.id)}
                    type="checkbox"
                  />
                </label>
                <div className="listing-main">
                  <div className="listing-title-line">
                    <strong>{listing.title}</strong>
                    {listing.application_id ? <span className="tag tag-success">In applications</span> : null}
                  </div>
                  <p>{listing.company_name} · {listing.location ?? "Location not listed"}</p>
                  <div className="tag-list">
                    {listing.role_families.map((family) => (
                      <span className="tag" key={family}>{roleLabels[family]}</span>
                    ))}
                    <span className="tag">{listing.career_stage === "new_grad" ? "New grad" : "Internship"}</span>
                    {listing.source_age_days !== null ? <span className="tag">{listing.source_age_days}d</span> : null}
                  </div>
                </div>
                <div className="listing-actions">
                  {listing.salary_text ? <span>{listing.salary_text}</span> : null}
                  {listing.application_url ? (
                    <a href={listing.application_url} rel="noreferrer" target="_blank">
                      Company listing <ExternalLink aria-hidden="true" size={13} />
                    </a>
                  ) : <span>Listing closed</span>}
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
