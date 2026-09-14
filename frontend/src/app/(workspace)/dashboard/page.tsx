"use client";

import { useAccount } from "@/components/workspace";

export default function DashboardPage() {
  const account = useAccount();
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Personal OS</p>
        <h1>Dashboard</h1>
        <p>The minimal hosted foundation is connected.</p>
      </header>
      <section aria-label="System status" className="status-list">
        <div className="status-row"><strong>Google identity</strong><span>{account.email}</span><span className="status-pill">Verified</span></div>
        <div className="status-row"><strong>Personal OS API</strong><span>Cloud Run identity and bootstrap endpoints</span><span className="status-pill">Connected</span></div>
      </section>
    </>
  );
}
