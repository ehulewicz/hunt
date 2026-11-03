# Graduation-date tailoring — parked 2026-09-14

Everything about answering a posting's graduation requirement with a different
date lives in this package. Nothing in the apply path imports it; the base
case attaches the canonical resume (December 2027) and answers every
"expected graduation" question from the profile (`graduation_date`,
2027-12-15). Pick this up once Greenhouse/Ashby/Lever form filling is verified.

## What exists and works (unit-tested, never run live end to end)

- `selection.py` — `with_graduation(resume, date)` patches only
  `education.graduation`, and `CANONICAL_GRADUATION` / `LATER_GRADUATION` are
  the only two dates the resume has ever carried. `requires_later_graduation(
  posting_text)` is one bounded `claude -p` call with a JSON schema; true only
  at `high` confidence when the posting explicitly requires graduating after
  December 2027 (minimum class year, "must graduate after X", "class of 2028
  or later"). The May 2028 PDF builder (`later_graduation_resume_pdf()`) no
  longer exists: the backend now builds the one resume PDF from the career
  record.

Until 2026-09-14 `apply_posting` called both: on a `true` it attached the
variant PDF and patched `resume_json` for the answer tiers. That wiring is
removed; see `git log -S requires_later_graduation -- workers/src` for the
exact lines.

## What is not done, and why it was parked

The resume date and the form's graduation answer must agree per application,
and today they cannot:

1. **The profile is global.** `graduation_date` lives on the single jobs
   profile, so the built-in `degree`/`school` rules and `PROFILE_LABELS` always
   answer December 2027 even when the May 2028 resume was attached. A
   per-application override is needed: the selection result has to reach
   `SessionContext.profile` (patch `graduation_date`, `graduation_month`,
   `graduation_year`) for that run only, and the Ashby education end-date
   fill reads those same keys.
2. **Never verified live.** No captured run has exercised the variant path;
   the fixture tests cover caching and the JSON contract only. A first live run
   should be a posting that plainly says "class of 2028" and a dry run to see
   which resume the log names.
3. **Honesty boundary.** The selection module's docstring keeps the rule: only
   which PDF is attached changes; an explicit "expected graduation date" form
   question is answered truthfully from the profile. Reconcile that with (1)
   before shipping — a per-run profile patch would change the form answer too,
   which is a decision, not a bug fix.

## To resume

1. Decide (3). If the form answer should follow the attached resume, patch the
   profile per run; if not, keep the profile and only swap the PDF.
2. Re-add the call in `jobs/apply_run.py` after `_reach_the_posting` returns
   `body`, guarded by `--no-claude`, and pass `resume_pdf` / `resume_json` (and
   the profile patch) into `SessionContext`.
3. Run live against one "class of 2028" posting with `--dry-run`, then for real.
4. Move `grad_date/selection.py` from parked to fill-time in the AGENTS.md
   model-call list.
