# Jobs CLI

All commands run from any directory after the editable worker install.

## Deterministic commands

```bash
os doctor
os hunt sync
os hunt listings
os hunt listings --family software_engineering --location "Atlanta, GA"
os hunt listings --all-roles
os hunt apps
os hunt answers list
os hunt answers add --question "Why this role?" --file ./answer.md
os hunt answers edit 1 --file ./revised-answer.md --approve

os hunt resume build
os hunt resume watch --open

os hunt prep 1
os hunt run

os hunt autofill --dry-run
os hunt autofill --resume ~/.local/state/personal-os/resume/main.pdf
```

`listings`, `apps`, and `prep` accept cached row numbers or unambiguous ID
prefixes where applicable. Answer edits update the hosted canonical library
immediately; Git is not a second answer store. Preparation without another flag
compiles the canonical resume unchanged.

## Explicit AI command

```bash
os hunt prep 1 --tailor
os hunt run
```

Only `--tailor` authorizes Claude for resume preparation. Claude or Codex may
invoke the same shell commands, but must not add `--tailor` unless the user asks
for tailoring. Sync, ranking filters, rendering, and form mapping have no model
dependency.

## Lentra-assisted fill

Install Lentra manually in the dedicated Windows Chrome profile, then run:

```bash
os hunt autofill --provider lentra --url "https://company.example/apply"
```

Playwright opens and verifies the form, pauses while the user clicks Lentra's
Fill control, and audits which labels are filled, required-and-empty, or
sensitive-and-filled. It never reads values or clicks Submit. Lentra is a
separate cloud service: its saved profile and resume are not canonical Personal
OS data. Review Lentra's current [privacy policy](https://getlentra.com/privacy/)
and [Chrome Web Store listing](https://chromewebstore.google.com/detail/lentra-job-application-au/dihlejpkfebcopfecdkbofbeolfkdbkf)
before installing it in the dedicated browser profile.
