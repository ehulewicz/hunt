"use client";

import { BriefcaseBusiness, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  batchPrepare,
  getListings,
  type JobListing,
  type SourceCategory,
} from "@/lib/jobs-api";

const categoryOptions: { value: SourceCategory; label: string }[] = [
  { value: "all", label: "All" },
  { value: "faang", label: "FAANG+" },
  { value: "quant", label: "Quant" },
];

function ageLabel(days: number | null) {
  if (days === null) return null;
  if (days === 0) return "Today";
  return `${days}d`;
}

export function ListingsWorkspace() {
  const accessToken = useAccessToken();
  const [items, setItems] = useState<JobListing[]>([]);
  const [total, setTotal] = useState(0);
  const [category, setCategory] = useState<SourceCategory>("all");
  const [locationOptions, setLocationOptions] = useState<string[]>([]);
  const [selectedLocations, setSelectedLocations] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getListings(accessToken, {
        category,
        locations: [...selectedLocations].sort(),
      });
      setItems(Array.isArray(page.items) ? page.items : []);
      setTotal(typeof page.total === "number" ? page.total : 0);
      setLocationOptions(Array.isArray(page.location_options) ? page.location_options : []);
      setSelected(new Set());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Listings could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [accessToken, category, selectedLocations]);

  useEffect(() => {
    let active = true;
    void getListings(accessToken, {
      category,
      locations: [...selectedLocations].sort(),
    })
      .then((page) => {
        if (!active) return;
        setItems(Array.isArray(page.items) ? page.items : []);
        setTotal(typeof page.total === "number" ? page.total : 0);
        setLocationOptions(Array.isArray(page.location_options) ? page.location_options : []);
        setSelected(new Set());
        setError(null);
      })
      .catch((caught) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "Listings could not be loaded.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [accessToken, category, selectedLocations]);

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

  function toggleListing(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleLocation(location: string) {
    setLoading(true);
    setSelectedLocations((current) => {
      const next = new Set(current);
      if (next.has(location)) next.delete(location);
      else next.add(location);
      return next;
    });
  }

  return (
    <div className="jobs-page">
      <header className="page-header jobs-page-header">
        <div>
          <p className="eyebrow">Jobs</p>
          <h1>Internships &amp; co-ops</h1>
          <p>
            SpeedyApply&apos;s SWE and AI/ML college lists, updated daily and always sorted newest first.
          </p>
        </div>
      </header>

      <section aria-label="Listing filters" className="jobs-toolbar">
        <fieldset className="jobs-filter-group">
          <legend>Category</legend>
          <div className="jobs-filter-options">
            {categoryOptions.map((option) => (
              <label className="jobs-filter-choice" key={option.value}>
                <input
                  checked={category === option.value}
                  name="listing-category"
                  onChange={() => {
                    setLoading(true);
                    setCategory(option.value);
                  }}
                  type="radio"
                  value={option.value}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <details className="location-filter">
          <summary>
            Locations
            {selectedLocations.size ? <span>{selectedLocations.size} selected</span> : null}
          </summary>
          <div className="location-filter-content">
            <div className="location-filter-heading">
              <strong>Match any selected location</strong>
              {selectedLocations.size ? (
                <button
                  className="text-button"
                  onClick={() => {
                    setLoading(true);
                    setSelectedLocations(new Set());
                  }}
                  type="button"
                >
                  Clear
                </button>
              ) : null}
            </div>
            <div className="location-options">
              {locationOptions.map((location) => (
                <label className="jobs-filter-choice" key={location}>
                  <input
                    checked={selectedLocations.has(location)}
                    onChange={() => toggleLocation(location)}
                    type="checkbox"
                  />
                  <span>{location}</span>
                </label>
              ))}
            </div>
          </div>
        </details>
      </section>

      <div aria-live="polite" className="jobs-feedback">
        {error ? <p className="notice notice-error">{error}</p> : null}
        {notice ? <p className="notice notice-success">{notice}</p> : null}
      </div>

      <section className="jobs-section">
        <div className="section-heading">
          <div><h2>Internships &amp; co-ops</h2><span>{total} matches</span></div>
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
          <div className="panel-state">No internship or co-op listings match.</div>
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
                    onChange={() => toggleListing(listing.id)}
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
                    {listing.source_category === "faang" ? <span className="tag">FAANG+</span> : null}
                    {listing.source_category === "quant" ? <span className="tag">Quant</span> : null}
                    {listing.normalized_locations.includes("Remote") ? <span className="tag">Remote</span> : null}
                  </div>
                </div>
                <div className="listing-actions">
                  {ageLabel(listing.source_age_days) ? <strong>{ageLabel(listing.source_age_days)}</strong> : null}
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
