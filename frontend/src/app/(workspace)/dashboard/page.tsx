"use client";

import { useAccount } from "@/components/workspace";

export default function DashboardPage() {
  const account = useAccount();
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Personal OS</p>
        <h1>Dashboard</h1>
        <p>The minimal hosted foundation is connected. Jobs is the next build milestone.</p>
      </header>
      <section aria-label="System status" className="status-list">
        <div className="status-row"><strong>Google identity</strong><span>{account.email}</span><span className="status-pill">Verified</span></div>
        <div className="status-row"><strong>Personal OS API</strong><span>Cloud Run identity and bootstrap endpoints</span><span className="status-pill">Connected</span></div>
        <div className="status-row"><strong>Jobs profile</strong><span>Minimal account row is ready for assisted setup</span><span className="status-pill">Ready</span></div>
      </section>
      <section className="next-panel"><h2>Next: Jobs</h2><p>We will begin with GitHub listings, then applications, resumes, cover letters, answers, and the local foreground worker.</p></section>
    </>
  );
}
