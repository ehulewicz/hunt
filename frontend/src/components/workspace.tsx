"use client";

import { useRouter } from "next/navigation";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { ApiError, type Account, authenticateAndBootstrap } from "@/lib/api";
import { getSupabase } from "@/lib/supabase";

type WorkspaceSession = { account: Account; accessToken: string };

const WorkspaceContext = createContext<WorkspaceSession | null>(null);

export function useAccount(): Account {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error("useAccount must be used inside the authenticated workspace");
  return workspace.account;
}

export function useAccessToken(): string {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error("useAccessToken must be used inside the authenticated workspace");
  return workspace.accessToken;
}

export function Workspace({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      const supabase = getSupabase();
      const { data, error } = await supabase.auth.getSession();
      if (!active) return;
      if (error || !data.session) {
        router.replace("/login");
        return;
      }
      try {
        const verified = await authenticateAndBootstrap(data.session.access_token);
        if (active) {
          setAccount(verified);
          setAccessToken(data.session.access_token);
        }
      } catch (error) {
        await supabase.auth.signOut({ scope: "local" });
        if (!active) return;
        if (error instanceof ApiError && error.status === 403) {
          router.replace("/login?reason=unauthorized");
        } else {
          setFailed(true);
        }
      }
    }
    void load();
    return () => { active = false; };
  }, [router]);

  async function signOut() {
    await getSupabase().auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  if (failed) return <div className="centered-state"><strong>Personal OS is unavailable.</strong><span>Refresh to try again.</span></div>;
  if (!account || !accessToken) return <div aria-label="Loading workspace" className="centered-state"><span className="spinner" /></div>;
  return <WorkspaceContext.Provider value={{ account, accessToken }}><AppShell account={account} onSignOut={() => void signOut()}>{children}</AppShell></WorkspaceContext.Provider>;
}
