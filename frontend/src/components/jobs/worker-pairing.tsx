"use client";

import { KeyRound, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAccessToken } from "@/components/workspace";
import {
  createWorkerCredential,
  getWorkerCredentials,
  revokeWorkerCredential,
  type WorkerCredential,
} from "@/lib/jobs-api";

export function WorkerPairing() {
  const accessToken = useAccessToken();
  const [credentials, setCredentials] = useState<WorkerCredential[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setCredentials(await getWorkerCredentials(accessToken)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Worker credentials could not be loaded."); }
  }, [accessToken]);
  useEffect(() => {
    let active = true;
    void getWorkerCredentials(accessToken).then((loaded) => {
      if (active) setCredentials(loaded);
    }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Worker credentials could not be loaded.");
    });
    return () => { active = false; };
  }, [accessToken]);

  async function create() {
    try {
      const issued = await createWorkerCredential(accessToken, "WSL Chrome runner");
      setToken(issued.token);
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Worker token could not be created."); }
  }

  async function revoke(id: string) {
    try { await revokeWorkerCredential(accessToken, id); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Worker token could not be revoked."); }
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "https://YOUR-CLOUD-RUN-API";
  return (
    <section className="form-section jobs-form">
      <div className="section-heading-inline"><div><h2>Local runner</h2><p>Pair this trusted WSL machine. The token is shown once and can be revoked here.</p></div><button className="button button-secondary" onClick={() => void create()} type="button"><KeyRound aria-hidden="true" size={15} />Create token</button></div>
      {error ? <p className="notice notice-error">{error}</p> : null}
      {token ? <div className="worker-token"><strong>Copy this token now</strong><textarea aria-label="New worker token" readOnly value={token} /><code>cd /home/eahtr/personal-os/workers &amp;&amp; UV_CACHE_DIR=.uv-cache uv run personal-os-worker pair --api-url {apiUrl}</code><span>Run the command in WSL, then paste the token into its hidden prompt.</span></div> : null}
      <div className="jobs-list">{credentials.map((credential) => <div className="credential-row" key={credential.id}><div><strong>{credential.name}</strong><span>{credential.revoked_at ? "Revoked" : `Expires ${new Date(credential.expires_at).toLocaleDateString()}`}</span></div>{!credential.revoked_at ? <button aria-label={`Revoke ${credential.name}`} className="button button-secondary" onClick={() => void revoke(credential.id)} type="button"><Trash2 aria-hidden="true" size={14} />Revoke</button> : null}</div>)}</div>
    </section>
  );
}
