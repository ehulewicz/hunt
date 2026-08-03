"use client";

import { Database } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { getSupabase } from "@/lib/supabase";

const UNAUTHORIZED_MESSAGE = "This Google account is not authorized for Personal OS.";
const FAILURE_MESSAGE = "Google sign-in could not be completed. Please try again.";

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [runtimeMessage, setRuntimeMessage] = useState<string | null>(null);
  const reason = searchParams.get("reason");
  const message = runtimeMessage
    ?? (reason === "unauthorized" ? UNAUTHORIZED_MESSAGE : null)
    ?? (reason === "callback" ? FAILURE_MESSAGE : null);

  useEffect(() => {
    void getSupabase().auth.getSession().then(({ data }) => {
      if (data.session) router.replace("/dashboard");
    });
  }, [router]);

  async function continueWithGoogle() {
    setBusy(true);
    setRuntimeMessage(null);
    const { error } = await getSupabase().auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
        queryParams: { access_type: "offline", prompt: "select_account" },
      },
    });
    if (error) {
      setRuntimeMessage(FAILURE_MESSAGE);
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-panel">
        <div className="brand-mark"><Database aria-hidden="true" /></div>
        <section className="login-card">
          <p className="eyebrow">Private workspace</p>
          <h1>Personal OS</h1>
          <p className="muted">Sign in with the one approved Google account.</p>
          {message ? <div className="auth-notice" role="alert">{message}</div> : null}
          <button className="button button-primary button-full" disabled={busy} onClick={() => void continueWithGoogle()} type="button">
            {busy ? "Opening Google…" : "Continue with Google"}
          </button>
          <p className="auth-footnote">No password or public account creation is available.</p>
        </section>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return <Suspense fallback={<main className="centered-state"><span className="spinner" /></main>}><LoginContent /></Suspense>;
}
