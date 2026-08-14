"use client";

import { Save } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import { getJobsProfile, type JobsProfile, updateJobsProfile } from "@/lib/jobs-api";
import { WorkerPairing } from "@/components/jobs/worker-pairing";

type Draft = Record<string, string>;

function value(profile: JobsProfile, key: keyof JobsProfile): string {
  const current = profile[key];
  if (Array.isArray(current)) return current.join(", ");
  if (typeof current === "boolean") return current ? "yes" : "no";
  return current === null ? "" : String(current);
}

function Field({
  label,
  name,
  draft,
  setDraft,
  type = "text",
  children,
  wide = false,
}: {
  label: string;
  name: string;
  draft: Draft;
  setDraft: (next: Draft) => void;
  type?: string;
  children?: ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={wide ? "form-field form-wide" : "form-field"}>
      <span>{label}</span>
      {children ?? (
        <input
          name={name}
          onChange={(event) => setDraft({ ...draft, [name]: event.target.value })}
          type={type}
          value={draft[name] ?? ""}
        />
      )}
    </label>
  );
}

export function JobProfileEditor() {
  const accessToken = useAccessToken();
  const [profile, setProfile] = useState<JobsProfile | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getJobsProfile(accessToken).then((loaded) => {
      if (!active) return;
      setProfile(loaded);
      setDraft(Object.fromEntries(Object.keys(loaded).map((key) => [key, value(loaded, key as keyof JobsProfile)])));
    }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Profile could not be loaded.");
    });
    return () => { active = false; };
  }, [accessToken]);

  async function submit(event: FormEvent, completed: boolean) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const saved = await updateJobsProfile(accessToken, {
        legal_name: draft.legal_name || null,
        preferred_name: draft.preferred_name || null,
        email: draft.email || null,
        phone: draft.phone || null,
        address_line_1: draft.address_line_1 || null,
        address_line_2: draft.address_line_2 || null,
        city: draft.city || null,
        region: draft.region || null,
        postal_code: draft.postal_code || null,
        country: draft.country || null,
        linkedin_url: draft.linkedin_url || null,
        github_url: draft.github_url || null,
        portfolio_url: draft.portfolio_url || null,
        work_authorization_status: draft.work_authorization_status || null,
        visa_status: draft.visa_status || null,
        sponsorship_required: draft.sponsorship_required === "" ? null : draft.sponsorship_required === "yes",
        school_name: draft.school_name || null,
        degree_type: draft.degree_type || null,
        field_of_study: draft.field_of_study || null,
        graduation_date: draft.graduation_date || null,
        gpa: draft.gpa || null,
        earliest_start_date: draft.earliest_start_date || null,
        earliest_start_text: draft.earliest_start_text || null,
        years_professional_experience: draft.years_professional_experience || null,
        remote_preference: draft.remote_preference || null,
        willing_to_relocate: draft.willing_to_relocate === "" ? null : draft.willing_to_relocate === "yes",
        willing_to_travel: draft.willing_to_travel === "" ? null : draft.willing_to_travel === "yes",
        target_roles: draft.target_roles?.split(",").map((item) => item.trim()).filter(Boolean) ?? [],
        target_locations: draft.target_locations?.split(",").map((item) => item.trim()).filter(Boolean) ?? [],
        target_career_stages: draft.target_career_stages?.includes("new_grad") ? ["internship", "new_grad"] : ["internship"],
        minimum_salary: draft.minimum_salary || null,
        professional_summary: draft.professional_summary || null,
        eeo_gender_identity: draft.eeo_gender_identity || null,
        eeo_race_ethnicities: draft.eeo_race_ethnicities?.split(",").map((item) => item.trim()).filter(Boolean) ?? [],
        eeo_veteran_status: draft.eeo_veteran_status || null,
        eeo_disability_status: draft.eeo_disability_status || null,
        completed,
      });
      setProfile(saved);
      setNotice(completed ? "Profile saved and marked ready for autofill." : "Profile saved.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Profile could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  if (!profile && !error) return <div className="panel-state"><span className="spinner" /> Loading profile…</div>;

  return (
    <div className="jobs-page">
      <header className="page-header">
        <p className="eyebrow">Jobs</p>
        <h1>Job Profile</h1>
        <p>Reusable application facts. Voluntary EEO values are optional and are never inferred.</p>
      </header>
      {error ? <p className="notice notice-error">{error}</p> : null}
      {notice ? <p className="notice notice-success">{notice}</p> : null}
      <form className="jobs-form" onSubmit={(event) => void submit(event, false)}>
        <section className="form-section">
          <h2>Identity and contact</h2>
          <div className="form-grid">
            {[["Legal name", "legal_name"], ["Preferred name", "preferred_name"], ["Email", "email"], ["Phone", "phone"], ["Street address", "address_line_1"], ["Address line 2", "address_line_2"], ["City", "city"], ["State or region", "region"], ["Postal code", "postal_code"], ["Country", "country"], ["LinkedIn", "linkedin_url"], ["GitHub", "github_url"], ["Portfolio", "portfolio_url"]].map(([label, name]) => <Field draft={draft} key={name} label={label} name={name} setDraft={setDraft} />)}
          </div>
        </section>

        <section className="form-section">
          <h2>Education</h2>
          <div className="form-grid">
            {[["School", "school_name"], ["Degree", "degree_type"], ["Field of study", "field_of_study"], ["Graduation", "graduation_date", "date"], ["GPA", "gpa", "number"]].map(([label, name, type]) => <Field draft={draft} key={name} label={label} name={name} setDraft={setDraft} type={type} />)}
          </div>
        </section>

        <section className="form-section">
          <h2>Application facts</h2>
          <div className="form-grid">
            {[["Work authorization", "work_authorization_status"], ["Visa status", "visa_status"], ["Earliest start date", "earliest_start_date", "date"], ["Earliest start note", "earliest_start_text"], ["Professional experience (years)", "years_professional_experience", "number"]].map(([label, name, type]) => <Field draft={draft} key={name} label={label} name={name} setDraft={setDraft} type={type} />)}
            {[["Sponsorship required", "sponsorship_required"], ["Willing to relocate", "willing_to_relocate"], ["Willing to travel", "willing_to_travel"]].map(([label, name]) => (
              <Field draft={draft} key={name} label={label} name={name} setDraft={setDraft}>
                <select onChange={(event) => setDraft({ ...draft, [name]: event.target.value })} value={draft[name] ?? ""}>
                  <option value="">Not answered</option><option value="yes">Yes</option><option value="no">No</option>
                </select>
              </Field>
            ))}
          </div>
        </section>

        <section className="form-section">
          <h2>Search preferences</h2>
          <div className="form-grid">
            <Field draft={draft} label="Target roles (comma separated)" name="target_roles" setDraft={setDraft} wide />
            <Field draft={draft} label="Target locations (comma separated)" name="target_locations" setDraft={setDraft} wide />
            <Field draft={draft} label="Career stages" name="target_career_stages" setDraft={setDraft}>
              <select onChange={(event) => setDraft({ ...draft, target_career_stages: event.target.value })} value={draft.target_career_stages ?? "internship"}>
                <option value="internship">Internships</option><option value="internship, new_grad">Internships and new grad</option>
              </select>
            </Field>
            <Field draft={draft} label="Remote preference" name="remote_preference" setDraft={setDraft} />
            <Field draft={draft} label="Minimum salary" name="minimum_salary" setDraft={setDraft} type="number" />
            <Field draft={draft} label="Professional summary" name="professional_summary" setDraft={setDraft} wide>
              <textarea onChange={(event) => setDraft({ ...draft, professional_summary: event.target.value })} value={draft.professional_summary ?? ""} />
            </Field>
          </div>
        </section>

        <section className="form-section">
          <h2>Voluntary EEO</h2>
          <div className="form-grid">
            <Field draft={draft} label="Gender response" name="eeo_gender_identity" setDraft={setDraft} />
            <Field draft={draft} label="Race or ethnicity responses" name="eeo_race_ethnicities" setDraft={setDraft} />
            <Field draft={draft} label="Veteran response" name="eeo_veteran_status" setDraft={setDraft} />
            <Field draft={draft} label="Disability response" name="eeo_disability_status" setDraft={setDraft} />
          </div>
        </section>

        <div className="form-actions">
          <span className="muted">{profile?.completed_at ? "Ready for autofill" : "Review before marking ready"}</span>
          <button className="button button-secondary" disabled={saving} type="submit">Save draft</button>
          <button className="button button-primary" disabled={saving} onClick={(event) => void submit(event, true)} type="button">
            <Save aria-hidden="true" size={15} /> {saving ? "Saving…" : "Save and mark ready"}
          </button>
        </div>
      </form>
      <WorkerPairing />
    </div>
  );
}
