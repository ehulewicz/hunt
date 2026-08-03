"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, authenticateAndBootstrap } from "@/lib/api";
import { getSupabase } from "@/lib/supabase";

export default function AuthCallbackPage() {
  const router = useRouter();
  const started = useRef(false);
  const [message, setMessage] = useState("Completing Google sign-in…");

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    async function complete() {
      const parameters = new URLSearchParams(window.location.search);
      const code = parameters.get("code");
      if (!code) {
        await getSupabase().auth.signOut({ scope: "local" });
        router.replace(
          parameters.get("error_code") === "signup_disabled"
            ? "/login?reason=unauthorized"
            : "/login?reason=callback",
        );
        return;
      }
      const supabase = getSupabase();
      const { data, error } = await supabase.auth.exchangeCodeForSession(code);
      if (error || !data.session) {
        await supabase.auth.signOut({ scope: "local" });
        router.replace("/login?reason=callback");
        return;
      }
      try {
        setMessage("Verifying your Personal OS account…");
        await authenticateAndBootstrap(data.session.access_token);
        router.replace("/dashboard");
      } catch (error) {
        await supabase.auth.signOut({ scope: "local" });
        router.replace(
          error instanceof ApiError && error.status === 403
            ? "/login?reason=unauthorized"
            : "/login?reason=callback",
        );
      }
    }
    void complete();
  }, [router]);

  return <main className="centered-state"><span className="spinner" /><span>{message}</span></main>;
}
