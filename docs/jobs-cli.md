# Jobs CLI

All commands run from any directory after the editable worker install.

## Deterministic commands

```bash
os doctor
os hunt sync
os hunt list
os hunt today
os hunt list 500  # overshooting returns every available match
os hunt list --family software_engineering --location "Atlanta, GA"
os hunt list --all-roles
os hunt open 12
os hunt open 12 --source
os hunt apps
os hunt answers list
os hunt answers add --question "Are you authorized?" --text "Yes" --reuse exact
os hunt answers add --question "Why this company?" --file ./answer.md --reuse contextual
os hunt answers add --question "consider me for other (job )?opportunities" --text "Yes" --reuse pattern
os hunt answers edit 1 --file ./revised-answer.md --reuse contextual --approve

os hunt resume build
os hunt resume watch --open

os hunt queue
os hunt queue add 3 7
os hunt queue empty
os hunt apps
os hunt apps 1 oa

os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply 1 --no-claude

os hunt apply failures list
os hunt apply failures show 1
os hunt apply failures diagnose 1
os hunt apply failures draft-fix 1
os hunt apply failures apply-fix 1
os hunt apply failures verify 1 7
```

Displayed listing titles are clickable when the terminal supports hyperlinks.
`os hunt open <row>` opens the direct company/ATS URL without creating an
application; `--source` opens the discovery URL. `list`, `queue`, `apps`, and
`apply` accept cached row numbers or unambiguous ID prefixes where applicable.
`queue add` puts listings in the queue; `queue empty` runs `apply` through it in
order, one interactive session at a time, and stops after any that is not
submitted. `apps` lists everything past the queue with its lifecycle status
(`applied`, `oa`, `oa_completed`, `interview`, `offer`, `rejected`); `apps
<row> <status>` moves one along. Answer edits update the hosted canonical library
immediately; Git is not a second answer store. `exact` answers are reused
unchanged. `pattern` rows hold a case-insensitive regular expression as the
question and answer any autofill question it matches, standing in for the
user's policy on things like "willing to relocate" without one row per exact
wording. `contextual` and unmatched long-form questions invoke local Claude,
with the 20 percent guard applied whenever a base exists. Preparation without another flag
compiles the canonical resume unchanged.

The editable source and generated PDF are both portable Git artifacts:

```text
artifacts/resume/main.json
artifacts/resume/Alex_Example.pdf
```

## Headed apply

Run `os hunt list`, then pass a displayed row or ID prefix:

```bash
os hunt apply 12
```

The command verifies the company/role, saves the tracked run/application context,
attaches the tracked `artifacts/resume/Alex_Example.pdf` unchanged, follows one unambiguous Apply
control when the ATS starts on a job-description page, fills the current step,
then pauses. Edit the real page, answer the one incorrect-field prompt, and
either type `review` or press Enter to authorize one exact Next/Continue click.
If that is unsafe or unavailable, the CLI says `Hit a wall` and keeps waiting
while you advance the visible browser manually. The CLI rescans after navigation.
At final review, type `approve` to reread and persist non-empty long-form answers.
No action vocabulary contains Next, Submit, consent, signature, attestation, or
captcha operations. In the terminal, Ctrl+S saves what has been picked up so
far, Ctrl+A saves and marks the application applied when you have submitted
it yourself, and Ctrl+C stops and saves. Whatever profile facts and exact answers leave unfilled goes
through library and built-in pattern rules, then one bounded local Claude pass
per page step over the remaining short and choice questions; `--no-claude`
skips that last pass.

Failure diagnosis and fix drafting use subscription-authenticated local Claude.
`apply-fix` requires the literal confirmation `apply`, changes only worker or
worker-test paths, runs the replay suite, and never commits. Commit the reviewed
fix yourself before `verify`; the command records that commit only after live
headed confirmation.
