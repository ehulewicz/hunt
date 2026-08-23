# Jobs CLI

All commands run from any directory after the editable worker install.

## Deterministic commands

```bash
os doctor
os hunt sync
os hunt listings
os hunt listings --limit 500  # overshooting returns every available match
os hunt listings --family software_engineering --location "Atlanta, GA"
os hunt listings --all-roles
os hunt apps
os hunt answers list
os hunt answers add --question "Are you authorized?" --text "Yes" --reuse exact
os hunt answers add --question "Why this company?" --file ./answer.md --reuse contextual
os hunt answers edit 1 --file ./revised-answer.md --reuse contextual --approve

os hunt resume build
os hunt resume watch --open

os hunt prep 1
os hunt run

os hunt autofill 1 --dry-run
os hunt autofill 1

os hunt autofill failures list
os hunt autofill failures show 1
os hunt autofill failures diagnose 1
os hunt autofill failures draft-fix 1
os hunt autofill failures apply-fix 1
os hunt autofill failures verify 1 7
```

`listings`, `apps`, and `prep` accept cached row numbers or unambiguous ID
prefixes where applicable. Answer edits update the hosted canonical library
immediately; Git is not a second answer store. `exact` answers are reused
unchanged. `contextual` and unmatched long-form questions invoke local Claude,
with the 20 percent guard applied whenever a base exists. Preparation without another flag
compiles the canonical resume unchanged.

The editable source and generated PDF are both portable Git artifacts:

```text
artifacts/resume/main.json
artifacts/resume/main.pdf
```

## Explicit AI command

```bash
os hunt prep 1 --tailor
os hunt run
```

Only `--tailor` authorizes Claude for resume preparation. Claude or Codex may
invoke the same shell commands, but must not add `--tailor` unless the user asks
for tailoring. Sync, ranking filters, rendering, and form mapping have no model
dependency.

## Headed autofill

Run `os hunt listings`, then pass a displayed row or ID prefix:

```bash
os hunt autofill 12
```

The command verifies the company/role, compiles and attaches
`artifacts/resume/main.pdf`, fills the current step, then pauses. Edit the real
page, answer the one incorrect-field prompt, and either type `review` or press
Enter before clicking Next/Continue yourself. The CLI rescans after navigation.
At final review, type `approve` to reread and persist non-empty long-form answers.
No action vocabulary contains Next, Submit, consent, signature, attestation, or
captcha operations.

Failure diagnosis and fix drafting use subscription-authenticated local Claude.
`apply-fix` requires the literal confirmation `apply`, changes only worker or
worker-test paths, runs the replay suite, and never commits. Commit the reviewed
fix yourself before `verify`; the command records that commit only after live
headed confirmation.
