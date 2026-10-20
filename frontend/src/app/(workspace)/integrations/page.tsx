"use client";

import { useEffect, useState } from "react";

import { useAccessToken, useAccount } from "@/components/workspace";
import { contactEmail } from "@/lib/api";

export default function IntegrationsPage() {
  const account = useAccount();
  const accessToken = useAccessToken();
  // undefined while loading, null when the contact.email fact is not set.
  const [inbox, setInbox] = useState<string | null | undefined>(undefined);
  const [inboxError, setInboxError] = useState(false);

  useEffect(() => {
    let active = true;
    contactEmail(accessToken)
      .then((email) => { if (active) setInbox(email); })
      .catch(() => { if (active) setInboxError(true); });
    return () => { active = false; };
  }, [accessToken]);

  const inboxLabel = inboxError ? "Could not load contact.email" : inbox === undefined ? "Loading…" : inbox ?? "contact.email is not set";

  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Personal OS</p>
        <h1>Integrations</h1>
        <p>Accounts Personal OS signs in with, and what each one may do.</p>
      </header>
      <section aria-label="Integrations" className="status-list">
        <div className="status-row status-head" aria-hidden="true"><span>Integration</span><span>Account</span><span>Access</span><span>Status</span></div>
        <div className="status-row">
          <strong>Google identity</strong>
          <span>{account.email}</span>
          <span>Sign-in to this workspace only, no Google data</span>
          <span className="status-pill">Verified</span>
        </div>
        <div className="status-row">
          <strong>Personal OS API</strong>
          <span>Cloud Run</span>
          <span>Identity, bootstrap and Jobs data, as the signed-in account</span>
          <span className="status-pill">Connected</span>
        </div>
        <div className="status-row">
          <strong>Gmail inbox</strong>
          <span>{inboxLabel}</span>
          <span>Read-only IMAP for ATS verification and reset mail, and Google sign-in in the automation Chrome</span>
          <span className="status-pill status-pill-muted">Checked by os doctor</span>
        </div>
      </section>
    </>
  );
}
