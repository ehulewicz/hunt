"use client";

import { ArrowUpRight, BriefcaseBusiness, Check, Circle, Clock3, Laptop, Search } from "lucide-react";
import { type FormEvent, useState } from "react";

import { HuntInbox } from "@/components/hunt-inbox";
import type { Device, HuntOverview } from "@/lib/api";

const STATUS_LABELS: Record<string, string> = { applying: "Pending", applied: "Applied", oa: "Assessment", oa_completed: "Assessment complete", interview: "Interview", offer: "Offer", rejected: "Rejected", skipped: "Skipped" };

export function ApplicationStart({ device, starting, error, onStart }: {
  device: Device | null; starting: boolean; error: string | null;
  onStart: (reference: string | null, fresh: boolean) => void;
}) {
  const [reference, setReference] = useState("");
  const [fresh, setFresh] = useState(false);
  const disabled = !device?.online || !device.ready || !!device.active_run_id || starting;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (reference.trim() && !disabled) onStart(reference.trim(), fresh);
  }
  return (
    <section aria-label="Start an application" className="application-start">
      <form onSubmit={submit}>
        <label className="sr-only" htmlFor="application-reference">Apply to</label>
        <div className="start-input"><Search aria-hidden="true" /><input disabled={disabled} id="application-reference" onChange={(event) => setReference(event.target.value)} placeholder="Paste a job URL, board number, or id" value={reference} /><button className="button button-primary" disabled={disabled || !reference.trim()} type="submit">{starting ? "Starting…" : "Start application"}<ArrowUpRight aria-hidden="true" /></button></div>
        <div className="start-options"><button className="text-button" disabled={disabled} onClick={() => onStart(null, fresh)} type="button">Next on board <ArrowUpRight aria-hidden="true" /></button><label className="run-fresh"><input checked={fresh} disabled={disabled} onChange={(event) => setFresh(event.target.checked)} type="checkbox" />New Chrome tab</label></div>
        {error && <p className="gap-error" role="alert">{error}</p>}
        {device?.active_run_id && <p className="muted">An application is already running on {device.name}. Open it below, or choose another device.</p>}
        {device && !device.ready && <p className="gap-error">{device.problem ?? "This device needs attention before starting."}</p>}
        {!device && <p className="muted">Choose an online device to start an application.</p>}
      </form>
    </section>
  );
}

export function HuntDashboard({ overview, devices, device, starting, startError, onStart, onOpen, onRefresh }: {
  overview: HuntOverview | null; devices: Device[]; device: Device | null; starting: boolean; startError: string | null;
  onStart: (reference: string | null, fresh: boolean) => void; onOpen: (device: Device) => void; onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<"Pending" | "All">("Pending");
  const applications = overview?.applications ?? [];
  const pending = applications.filter((application) => application.status === "applying");
  const visible = filter === "Pending" ? pending : applications;
  const running = devices.filter((item) => item.active_run_id);
  const metrics = [
    { label: "Applied", value: applications.filter((application) => application.applied_at !== null).length, icon: Check },
    { label: "Pending", value: pending.length, icon: Clock3 },
    { label: "On the board", value: overview?.available ?? 0, icon: ArrowUpRight },
  ];

  return (
    <div className="hunt-dashboard">
      <div aria-label="Application totals" className="hunt-metrics" role="group">
        {metrics.map(({ label, value, icon: Icon }) => <div aria-label={label} className="hunt-metric" key={label}><div><span>{label}</span><Icon aria-hidden="true" /></div><strong>{overview ? value.toLocaleString() : "—"}</strong></div>)}
      </div>
      <ApplicationStart device={device} error={startError} onStart={onStart} starting={starting} />
      <section aria-label="Applications" className="dashboard-panel applications-panel">
        <header className="panel-header"><h2>Applications</h2><div aria-label="Application filter" className="segment-control compact">{(["Pending", "All"] as const).map((name) => <button aria-pressed={filter === name} key={name} onClick={() => setFilter(name)} type="button">{name}{name === "Pending" && <span>{overview ? pending.length : "—"}</span>}</button>)}</div></header>
        <div className="panel-scroll application-list">
          {running.map((item) => <button aria-label={`Open application at ${item.run?.company_name ?? item.name}`} className="active-application" disabled={!item.online} key={item.id} onClick={() => onOpen(item)} type="button"><span className="status-dot" /><span><small>Open on {item.name}</small><strong>{item.run?.company_name ?? "Application in progress"}</strong><span>{item.run?.title ?? "Open the session to continue"}</span></span><ArrowUpRight aria-hidden="true" /></button>)}
          {visible.length === 0 ? <div className="panel-empty"><BriefcaseBusiness aria-hidden="true" /><h3>{overview ? filter === "Pending" ? "A fresh start" : "Your story starts here" : "Loading your applications"}</h3><p>{overview ? filter === "Pending" ? "No applications are waiting. Start with the next job on your board." : "Start an application and follow its progress here." : "Your application history will appear here."}</p></div>
            : visible.map((application) => <div className="application-row" key={application.id}>
              <span aria-hidden="true" className="company-avatar">{application.company_name.slice(0, 1)}</span>
              <div className="application-description"><strong>{application.company_name}</strong><span>{application.title}</span><small>{application.unanswered_required_questions.length > 0 && application.status === "applying" ? `${application.unanswered_required_questions.length} required ${application.unanswered_required_questions.length === 1 ? "answer" : "answers"} to finish` : application.next_action ?? application.location ?? "Application recorded"}</small></div>
              {application.status === "applying" ? <button aria-label={`Resume application at ${application.company_name}`} className="button button-secondary resume-button" disabled={starting || !device?.online || !device.ready || !!device.active_run_id} onClick={() => onStart(application.posting_id, false)} type="button">Resume<ArrowUpRight aria-hidden="true" /></button> : <span className={`application-status ${application.status === "interview" || application.status === "offer" ? "positive" : ""}`}>{STATUS_LABELS[application.status] ?? application.status}</span>}
            </div>)}
        </div>
        <footer className="panel-footnote"><Circle aria-hidden="true" />{overview ? `${pending.length} pending ${pending.length === 1 ? "application" : "applications"}` : "Waiting for application history"}<span><Laptop aria-hidden="true" />{devices.filter((item) => item.online).length} online</span></footer>
      </section>
      <HuntInbox computed={overview?.computed ?? []} facts={overview?.facts ?? []} inbox={overview?.inbox ?? null} onRefresh={onRefresh} />
    </div>
  );
}
