# Answer tiers for short and choice questions

Status: approved in conversation on 2026-09-13, not yet implemented.

## Problem

`os hunt autofill` fills a non-textarea field only from a profile fact or an
exact library answer keyed on the literal question text. Employer-specific
wording ("…other job opportunities within RV Tech…") therefore never reuses
across companies, and every yes/no policy question is left for the user on
every form even though the answer never changes.

## Decision

Resolve each unfilled, non-consent, non-textarea field through three tiers in
order, stopping at the first that answers:

1. **Profile fact or exact library answer** (exists today).
2. **Pattern rule** (deterministic). A library row with `reuse_policy =
   'pattern'` whose `normalized_question` is a case-insensitive regular
   expression and whose `answer_text` is the answer. Exactly one matching rule
   fills the field; zero or several leave it alone. One built-in rule needs no
   row: a question asking whether the applicant has ever worked for, been
   employed by, or interned at the company answers with the option beginning
   "No" when the company name matches no organization on the canonical
   resume, and is left for the user when it does.
3. **Claude short-answer pass** (one `claude -p` call per page step, only for
   fields the first two tiers left empty and only when the field is required
   or has a fixed option list). Claude sees the questions with their exact
   options, the profile, the canonical resume, the posting, and every approved
   library row. It returns one answer per question with `high` or `low`
   confidence; only `high` answers are used, choice answers must equal one of
   the offered options verbatim, and free-text answers must pass the existing
   number and cliche guards. Anything else is left for the user.

Answers from tiers 2 and 3 are echoed in the terminal with their tier and are
saved on Submit, Ctrl+S, or Ctrl+C as exact answers under the literal
question, so the same form is deterministic next time. Consent, attestation,
privacy, and signature controls are never filled by any tier.

Choice matching is relaxed for tiers 2 and 3: an answer selects the single
option in the group that equals it, starts with it, or contains all of its
words. "Yes" against a group with several "Yes - …" options stays ambiguous
and is not filled.

## Schema change (confirmed with the user)

`app.jobs_answer_library.reuse_policy` gains the value `'pattern'`. No new
columns or tables.

## Convention change

AGENTS.md currently forbids implicit model calls in form filling. This design
adds one bounded, opt-out call per page step that only runs after the
deterministic tiers and only decides among visible options. AGENTS.md is
updated to say exactly that; `--no-claude` on `os hunt autofill` disables it.

## Out of scope

Claude proposing new pattern rules; patterns are authored with
`os hunt answers add --reuse pattern`. Workday. Textarea drafting, which keeps
its existing VS Code tab flow.
