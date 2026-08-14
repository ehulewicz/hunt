# Jobs worker

This trusted foreground worker prepares tailored resumes and visibly fills job
applications. It never clicks Submit. Chrome stays open after autofill so the
application can be reviewed and submitted manually.

## Set up and pair

From WSL Ubuntu:

```bash
cd /home/eahtr/personal-os/workers
UV_CACHE_DIR=.uv-cache uv sync
```

In Personal OS, open **Jobs → Profile → Local runner**, create a credential, and
copy the pairing command shown there. Run it in WSL and paste the one-time token
when prompted. The token is stored at `~/.config/personal-os/worker.json` with
mode `0600`.

## Launch

From Windows PowerShell:

```powershell
& "\\wsl.localhost\Ubuntu\home\eahtr\personal-os\workers\run.ps1"
```

PowerShell delegates to `run.sh`. The script launches `/usr/bin/google-chrome`
through WSLg with the dedicated profile at
`~/.local/share/personal-os/chrome`, exposes CDP only on
`http://127.0.0.1:9222`, runs the doctor checks, and starts the worker.

To diagnose without starting the polling loop:

```bash
cd /home/eahtr/personal-os/workers
UV_CACHE_DIR=.uv-cache uv run personal-os-worker doctor
```

The doctor verifies pairing and hosted API access, Chrome CDP, Claude
authentication, `latexmk`, and `pdfinfo`.

## Recovery and revocation

- If Chrome is closed, rerun `run.ps1`; the dedicated profile is reused.
- If a task loses its lease or the API is temporarily unavailable, safe
  transient failures remain queued and the worker can be restarted.
- If pairing expires or is revoked, create a new credential in Profile and run
  the new pairing command.
- To revoke this machine, use **Revoke** beside its credential in Profile, then
  delete `~/.config/personal-os/worker.json`. Revocation immediately prevents
  further claims, heartbeats, uploads, and completion calls.
