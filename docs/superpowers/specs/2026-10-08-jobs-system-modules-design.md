# Jobs system: modules, registry, and the application path

Date: 2026-10-08, revised 2026-10-14 and 2026-10-15. Status: slices A (Facts)
and D (Registry) were built 2026-10-08; the 2026-10-14 revision (§0) restarts the
learned knowledge from scratch and reshapes slice C, and B, C, E, and F were
built under it the same day. The 2026-10-15 revision (R53–R55) removes per-site
adapters, takes the archive out of applications, and makes repair in place.

This document describes the Jobs system as a set of modules with explicit
boundaries, and specifies them in enough detail to build. It replaces the
implicit structure that grew inside `workers/` with named parts, measurable
rules, and a path that gets more deterministic over time.

The schema in §12 was approved on 2026-10-08. The entries marked **added after
approval**, R32–R39, and the §0 revision were decided by Claude on 2026-10-14
under your delegation ("go ahead of your own volition, report the decisions");
they are listed in §17 as *delegated* so you can dispute any by number.

**Reading the recommendations.** Where the design was unclear, a decision has
been taken and marked as a numbered recommendation:

> **R0 — Example.** The decision, then why.

Each is a default, not a question. Disagree with one by number; anything not
disputed is treated as agreed. §17 indexes them all with their status; the text
of a recommendation you changed already reflects your decision.

---

## 0. Revision 2026-10-14: start from scratch

You decided to restart what the system has *learned* so the training loop can
be watched working from an empty state: every fact re-entered and verified in a
session, the old answers, patterns, and policies archived, and the archive kept
as a reference that the new system consults when it meets something the old one
knew, adding it back in translated form. Everything below §1 still holds except
where this section changes it; the changed passages point back here.

**What is knowledge and what is engine.** Knowledge is anything that says what
is true about you or how you answer: facts, prose, questions, patterns. It
restarts empty and is learned. The engine is how a form is read and operated:
control kinds, strategies, formatters, readback, captcha and submit guards, file
uploads, consent handling. It stays active, because it holds nothing about you
to unlearn.

> **R40 — No pattern is active on day one.** The 26 builtin rules in
> `patterns.py`, the priority policies in `resolve_priority_answer`, the
> exact-label map `PROFILE_LABELS`, and the two library regex rows all move to
> the archive. Supersedes R27 and the sort-with-you migration of §14.

> **R41 — The archive answers *which fact*, never *what value*.** *Superseded by R54.* An archive
> hit names the fact a question asks about; the value always comes from that
> fact, through a formatter. An archived answer that maps to no fact ("Search →
> python async", "When are you available to start? → Yes") is shown to you as a
> hint and never typed. This is what keeps "May 2028" and "$35" from coming back.

> **R42 — The archive lives where the old data already is, frozen.**
> `jobs_answer_library` becomes `jobs_archive_answers`, `jobs_profile` becomes
> `jobs_archive_profile`, and `jobs_documents` (the stale resume copy and two
> tailored resumes) becomes `jobs_archive_documents`; the API role keeps only
> `select` on them. The retired rules move to `workers/…/jobs/archive/`, each
> carrying its translation: the fact key it reads and the formatter that renders
> it. Long-form archive text is readable with `os hunt archive prose`, which can
> write it to a Markdown file of your choosing; no export is committed.

> **R43 — An archive or model fill proposes, it does not decide.** *Superseded by R54.* When the
> archive (or the model pass, §7.4) supplies the fact for a question, the field
> is filled from that fact and tagged `archive` (or `model`) in the terminal, and
> a `candidate` binding is recorded. Nothing becomes `active` until you accept it
> in `os hunt inbox`; the next application then fills that question
> deterministically, tagged `pattern`. That is the loop, end to end, and each
> turn of it is visible.

> **R44 — Questions are recorded when they are met.** Every fill event upserts
> its question into `jobs_questions` (first and last seen), not only questions
> someone dispositioned. The Inbox is then a view over rows (R38) from the first
> application. Amends §7.4's "rows record dispositions".

> **R45 — Engine rules stay active and are not patterns.** Consent and
> attestation controls take their agreeing option; signatures are left to you;
> resume, transcript, and cover letter uploads are detected by the control; the
> captcha and submit guards hold; non-answer options ("Prefer not to say") are
> never chosen; "How did you hear about us?" ranks the employer's own site, then
> a job board, then Other. These are your standing behavior rules, decided
> earlier, not facts.

> **R46 — Strategies stay active; controls are learned by observation.** The
> thirteen seeded strategies are mechanics that work today. `jobs_controls` is
> empty and fills as widgets are met, so the control side of the loop is visible
> in the fill events from the first run. Starting strategies as `candidate`
> would mean validating thirteen things that already operate, for no
> information.

> **R47 — The Inbox is a command now, not later.** `os hunt inbox` walks
> candidate patterns, unbound questions, unapproved prose, and (with F) repaired
> rules. Starting from scratch makes it central from the first application, so
> it moves from "Later" into slice C.

> **R48 — Accepting a candidate activates it.** A candidate binding carries its
> fill evidence (the value it filled and whether you kept it), which is what
> `validated` means; accept moves it to `active` in one step. `validated` remains
> the state a repaired rule reaches through `hunt validate` (§10.5).

> **R49 — An archived rule's expression is adopted on request.** *Superseded by R54.* A binding
> translated from an archived rule can be widened, from the Inbox, into that
> rule's old regular expression as an `expression` pattern (origin `archive`), so
> every phrasing resolves. The Inbox offers it once two accepted bindings come
> from the same rule (the second-sighting rule, R10).

> **R50 — Old failures are archived, not deleted.** The 254 open failure issues
> came from the old dispatch; they move to status `archived` so the repair queue
> (F) starts empty and the history stays queryable. Runs left `running` since
> September are marked `abandoned`. The five applications still `applying` are
> left for you.

> **R51 — Stored facts shadow career-derived ones until you remove them.** Slice
> B derives education, tenure, employer, and skills facts from the career record,
> but the seeded stored rows for the same keys are not deleted: a stored fact
> wins, and `os hunt facts list` marks it as shadowing the derived value. The
> facts session settles each one. Deleting them silently would change values
> (`education.degree_type` "Bachelor's" vs the career record's "BS", tenure 4 vs
> the derived 2) behind your back.

> **R52 — In-place mode waits for an account-gated adapter.** *Superseded by R53.* Workday is not
> supported by `hunt apply` today, so §10.4 has nothing to run on. Repair (F)
> ships for the single-page ATSs (Greenhouse, Ashby, Lever), where "advance to
> the failing step" is the first page; in-place mode lands with the first
> account-gated adapter.

The facts session (seed review, the catalog keys the old answers show you need,
and settling R51's shadows) happens with you after this lands.

### Revision 2026-10-15

> **R53 — No adapters: every site is filled from its controls.** The point of
> the registry is that what is learned about a control on one board answers the
> same control on any other, so nothing branches on the board. `hunt apply`
> runs on any URL, generic sites included; the run's `ats` (`greenhouse`,
> `ashby`, `lever`, `workday`, `generic`) is a label for querying runs and
> failures, never behavior. Per-site adapters may come later, built on top of
> this, not instead of it. *Decided by you.* Supersedes R52.

> **R54 — The archive is for the facts session only, then dropped.** No
> application reads the archive, not even as a suggestion: it is not in a run's
> context, not a resolution tier, and not reference for a long-form draft. It
> exists so the facts session can settle every fact against what the old system
> said, and is dropped right after. A candidate binding comes only from a model
> fill that read back. *Decided by you.* Supersedes the fill-time half of R41,
> the archive half of R43, and R49.

> **R55 — Repair runs in place.** Under `--repair`, each field that stopped at a
> format or a strategy is retried on the live page right after that step's
> fill, on any site — account-gated boards included, because the page is
> already signed in. What reads back fills the field now and is stored as the
> issue's candidate; `os hunt validate` still decides whether it becomes the
> rule. There is no background worker. `os hunt repair` keeps the isolated
> context (R22, R23) for failures from runs without `--repair`. *Decided by
> you: less overhead.* Supersedes R24 and R25.

### Revision 2026-10-16

> **R56 — Repair in place is on for every application.** The flag made the
> default the worse behavior: a field that fails has a live page in front of it
> and nothing is gained by leaving it. `--no-repair` turns it off and sends the
> failure to `os hunt repair`'s isolated context instead. *Decided by you.*
> Amends R55.

> **R57 — Apply continues on the tab already open for this application.**
> `VisibleBrowser.application_page` reuses a tab whose URL the application URL
> is a prefix of — every board keeps the posting in the path once the form
> opens — and brings it to the front without navigating, so stopping a run,
> changing code, and re-running keeps whatever is already typed into the form.
> `--fresh` opens a new tab. *Decided by you.*

> **The landing page is read after it renders.** Not a recommendation, a defect:
> the page's text was read once, right after `domcontentloaded`, which on a
> board that fills its page from script is `<div id="root"></div>`. The Northrop
> Grumman Workday posting failed verification for a page that said nothing yet,
> and the same empty text would have been the posting context the cover letter
> and long-form drafts are written from. `rendered_body_text` polls instead:
> it returns as soon as the page names the posting, else once the text has
> stopped changing, else at the deadline.

> **R58 — A gate is a choice control, answered by a fact.** Boards put a choice
> between Apply and the form (Workday: autofill from a resume, apply manually,
> reuse the last application). Nothing on such a page is an input, so field
> capture saw an empty page and the run hit a wall with no way to learn past it
> — the per-site adapter surviving as the Apply regex. `jobs/gateway.py`
> captures those ways in as one `choice_group`: question from the dialog's
> heading, options from the buttons, answer from
> `preferences.application_start` through the ordinary resolver. Nothing is
> clicked without a fact behind it; the page's own submit, sign-in and
> account controls are never candidates; the readback is the form appearing. A
> gate no fact answers is left for you, and the choice you click is recorded as
> that question's answer so the Inbox can bind it. *Decided by you.*

> **R59 — The Inbox can widen a binding into an expression.** Expression
> patterns were readable, matchable and validated but uncreatable: R54 removed
> the archive's widen step, which had been their only source, so every active
> pattern was a per-question binding. Binding a question now offers a regular
> expression — proposed from the question's own distinctive word, or typed —
> which answers every question matching it from the same fact, including
> phrasings no application has used yet. *Found in review.*

---

## 1. Why

Applying works today but cannot improve itself, and its answers can contradict
each other.

**Successes are not recorded.** A control that fails gets a
`failure_signature(ats, category, fingerprint)`. A control that succeeds records
nothing. A success *rate* therefore cannot be computed — the denominator does
not exist.

**Fills are not attributable to a rule.** The answer side carries
`AnswerResolution(value, source, resolver_id)`, which is good. The control side
has only a per-page fingerprint that includes the question label, so the same
widget on two companies looks like two different things and observations never
accumulate.

**The control taxonomy is implicit and ATS-contaminated.** The dispatch in
`browser.py` branches on four unrelated axes — `fill.kind` (answer semantics),
`field.tag` (HTML element), `field.role` (ARIA), `field.type` (HTML type plus a
synthetic `yesno` detected from an Ashby CSS class) — with a Lever-specific
captcha guard inside one branch. It works, and it cannot be reasoned about,
extended, or measured.

**A fact is stored many times, and the copies disagree.** Short-form answers
live in four places: `jobs_profile` columns, `exact` rows in
`jobs_answer_library`, `pattern` rows in the same table, and Python rules in
`patterns.py`. The library is labelled long-form — every one of its 198 rows
has `answer_type = 'long_form'` — but 188 are `exact` rows averaging 24
characters. Graduation date appears as "Fall 2027", "December 2027", "Fall 2027
(December 2027)", and "May 2028". About 25 rows restate one sponsorship and
authorization fact, one string per phrasing, so phrasing 26 still misses.

**Time-dependent facts are frozen.** `completed_coop_terms = 4.5` is a stored
number with no date, and it goes wrong the day a term ends.

**The resume is a display string, not a record.** `artifacts/career/main.json`
stores dates as `"May 2025 – Present"`. Nothing can compute tenure, prior
employers, or graduation from it without parsing prose, so the same facts are
re-typed into the profile and the library instead.

The goal is a system where every fact is stored once, every fill is attributable
to exactly one pattern and one control, every outcome is a measurement, and
accumulated measurements move fills down a ladder from manual, to model-assisted,
to fully deterministic — without ever stalling the application in front of you.

---

## 2. Current state

### Exists and is kept

- `AnswerResolution(value, source, resolver_id)` — `autofill.py:14`. Stable
  resolver ids already in use: `builtin:profile_field_v1`, `library:{id}`,
  pattern rule names.
- Per-field detection returning `tag, type, name, label, group, required,
  selector, role, max_length, placeholder, autocomplete, section, options[],
  value, filled` — `browser.py:840-888`. One read yields the question (label),
  the control (tag, role, type), and the available responses (options).
- `verify_control` readback, including the upload and combobox mirror paths —
  `browser.py:575`.
- Eight operation methods, six of them already named functions:
  `set_input_files`, `option_match` + `select_option(label=)`,
  `select_option(value=)`, `choose_combobox_option`, `select_combobox_option`,
  button click + `aria-pressed` verify, `control.check()`,
  `set_registered_value`.
- Option choosers usable as formatters: `choose_option`, `choose_synonym`,
  `choose_numeric_range` — `autofill.py:497-596`.
- Failure capture and the `list | show | diagnose | draft-fix | apply-fix |
  verify` pipeline — `commands.py:647`, with its issue table and replay tests.
- Answer history with provenance in `jobs_application_questions`.

### Exists and is replaced

- `jobs_profile` — superseded by `jobs_facts` (§7), then archived (R42).
- `jobs_answer_library` — archived whole (R42); a question it answered is
  re-learned in translated form the next time it is met (§14).
- `resolve_priority_answer` — `patterns.py:378`. It mixes three jobs: choosing
  a fact, deriving a value (salary × 2080), and formatting it for the widget.
  Those split into derived facts and formatters.

### Missing

- Any record of a successful fill at control grain.
- Stable, ATS-independent control identity.
- Named strategies; they are if/elif branches.
- `uncheck`. `.check()` is the only check operation in the file.
- `select[multiple]` handling.
- Distinction between radio, lone checkbox, and checkbox group — all one branch.
- A way to repair a failure without stopping or restarting the live run.

---

## 3. Modules

| Module | Does | Owns | Status |
|---|---|---|---|
| **Facts** | Base facts about you, derived facts computed from them | what is true about you | built (A) |
| **Career record** | Your resume as structured rows: education, experience, research, projects, bullets, skills | what you have done | replaces `main.json` as the source |
| **Prose** | Saved long-form writing and how a new question matches it | your voice | new, starts empty (R40) |
| **Registry** | Deterministic fill rules: questions, patterns, formatters, controls, strategies | *how* to fill | instrumentation built (D) |
| **Archive** | The old answers, rules, profile, and documents, frozen, consulted on a miss | what the old system knew | new (R42) |
| **Apply** | Runs one application: open, read, fill, verify, submit | browser session, run record | exists |
| **Repair** | Fixes failed fills in a separate browser while you keep applying | candidate rules | new; extends the failure pipeline |
| **Inbox** | One human queue: new questions, repaired rules awaiting validation, unwritten long-form, audit flags | everything waiting on you | `os hunt inbox`, slice C (R47) |
| **Audit** | Judges filled pairs for correctness before submit | the correctness verdict | new |
| **Condenser** | Offline loop turning history into new or merged rules | rules from evidence | new |

### Flow

```
                 ┌──────────── Facts ◄──── you (new fact) ◄──────┐
                 │               │                                │
                 ▼               ▼                                │
   hunt apply ──► Apply ──► fill_events ──► Condenser ──► proposals ─► Inbox
                   │  ▲          ▲                                    ▲  │
     any failure   │  │ you fill │                                    │  │
     (never stops) │  │ by hand  │                                    │  │
                   ▼  │          │                                    │  │
                 Repair (second browser) ──► candidate rule ──────────┘  │
                                                                         │
                         hunt validate ◄─────────────────────────────────┘
                              │
                              └──► Registry (validated rule) ──► next Apply

   Apply page complete ──► Audit (sampled) ──► verdicts ──► Inbox ──► submit
```

### Build order

The work is cut into slices with explicit file ownership and dependencies, so
several can be built at once without colliding. §16 has the full breakdown.

```
A Facts ──┬──► B Career record
          ├──► C Answers (questions, patterns, prose)
          │         │
D Registry ─────────┴──► E Fill policy ──► F Repair and validate
```

Later, in order: Inbox, Audit, Condenser, unattended batch.

> **R1 — Facts go first, and Repair moves ahead of Audit.** Patterns point at
> facts, so the fact store has to exist before patterns can be rebuilt on it.
> Repair moves up because you are applying now: every failure that the repair
> lane turns into a validated rule pays off on the very next application,
> whereas Audit only pays off once the Condenser exists to act on its verdicts.
> Registry instrumentation (D) depends on none of the answer work and runs in
> parallel from the start.

---

## 4. Core decisions

**A fact is stored once and formatted many ways.** Graduation is one date; "Fall
2027", "12/2027", and a term dropdown are renderings of it, produced at fill
time. A per-phrasing copy of a fact is never stored, because copies are how
"May 2028" got in.

**Time-dependent facts are derived, never stored.** A derived fact is a function
of base facts, today's date, and sometimes the posting. Nothing stores
"completed rotations"; it is computed when asked.

**A control is the widget, nothing else.** Not the ATS, not the question, not
the answer's semantics. The test: *two widgets are the same control if and only
if the same strategy operates them.* This is what stops the taxonomy drifting
back into semantics, which is how the current dispatch came to mix `kind` with
`tag`, `role`, and `type`.

**ATS never enters control identity.** Relying on ATS markers means the system
never learns to handle non-standard career sites (Amazon, Google) — only the
templated ones (Greenhouse, Workday, Ashby, Lever). Those are exactly the sites
that cannot be hardcoded for, so generic detection is the requirement, not a
refinement. ATS remains available as a *dimension* on fill events for querying,
never as part of a control's identity.

**ARIA is the generic substrate.** Custom widgets must expose state to screen
readers to be usable, and for careers pages that is often a legal requirement.
`aria-pressed`, `aria-checked`, `role=radiogroup`, `role=switch` are therefore
more reliable cross-site signals than any CSS class. The existing yes/no
detection already reads `aria-pressed` for state — it just reaches it through
two Ashby-specific hops.

**Control and strategy are separate.** A control is the widget kind; a strategy
is a named method of operating it. One control may have several candidate
strategies with precedence — the existing `select` path already does this
informally, trying `select_option(label=)` and falling back to
`select_option(value=)` in its `except` block.

**One pattern and one control per fill.** This is the keystone. Without it, a
success rate tells you nothing about what succeeded. Formatter and strategy are
recorded as dimensions on the event, not as further chosen components.

**The live run never restarts on a fixable error.** A failure is recorded, the
field is handed to you, and the run continues. With `--repair`, fixing happens
in parallel in a second browser (§10.3). You are applying now; the system
learns beside you, not in front of you.

**No field is left empty.** Required or optional, every field is filled; the run
does not advance past a page while any field is unfilled. A field stays blank
only when a fact says the true answer is empty. When every rule and the model
pass have come up empty, it waits for you (§10.2).

**The cover letter is always attached.** If the form offers a cover letter, by
upload or by text, it gets one, whether or not it is required.

**No stored counters.** `fill_events` is the only thing written per field during
a run. Every rate — per control, per pattern, per pair, per strategy, per ATS —
is a query over it. There is no two-phase counter update to tangle with the
atomic run-completion transaction, and no marginal counter that can drift from
the pair counter it should agree with.

**Everything retires, nothing is deleted.** Patterns, formatters, and strategies
carry a status; reads use active rows only. Condensing retires the superseded
rows rather than removing them, which makes a merge reversible.

**Randomness is seeded per application.** Where a policy says "pick at random"
(§9), the draw is seeded by the application id. Different applications get
different picks; a reset, a repair, or a validation replay of the same
application gets the same pick, so behavior stays reproducible and testable.

**The determinism ladder.** Three rungs, for answers and for controls:

1. manual — you do it
2. model-assisted — deterministic narrowing to N candidates, model picks one
3. deterministic — a single rule

Each rung's output is the next rung's evidence. The model's choices at rung 2
are precisely the signal the Condenser uses to collapse N to 1. A rule that
stays at rung 2 forever is visible and measurable rather than hidden
over-fitting.

**Probing is never done on the live application.** Clicking a widget to learn
how it behaves mutates the form, can fire side effects, and is not always
reversible. It happens only in the repair browser (§10.3), or on the current
page of a run that opted into in-place mode (§10.4, R36).

**CAPTCHAs are detected and handed off, never solved.** Detection becomes
generic by dropping the `ats == "lever"` conjunct; the selectors are already
generic. A captcha present means the field is handed to you. Circumventing
anti-automation measures is out of scope for this system.

---

## 5. Control taxonomy

Generated by three axes: **payload** (value or file), **options** (free, present
in DOM at rest, or only after interaction), **cardinality** (one, many,
boolean).

| Kind | Options | Cardinality | Covers |
|---|---|---|---|
| `text_entry` | free | one | `input[text,email,tel,url,number,password]`, `textarea` |
| `native_select` | in DOM | one | `<select>` |
| `choice_group` | in DOM | one | radio group, `role=radiogroup`, `aria-pressed` button group |
| `multi_choice_group` | in DOM | many | checkbox group, `select[multiple]` |
| `toggle` | — | boolean | lone checkbox, `role=switch` |
| `dynamic_choice` | on interaction | one | `role=combobox` with empty `options[]` |
| `file` | — | file | `input[type=file]` |

`textarea` collapses into `text_entry` because the same strategy operates it.
The long-form/short-form distinction is a property of the *question*, already
routed off `max_length` and `tag`, not of the widget.

### Classification

From attributes already captured, plus two new detection bits:

```
type = file                                      → file
tag = select                                     → native_select
    with multiple                                → multi_choice_group
role = combobox and options[] empty              → dynamic_choice
type = radio, or role = radiogroup,
    or >= 2 siblings carrying aria-pressed       → choice_group
type = checkbox, alone in its group              → toggle
type = checkbox, with siblings in its group      → multi_choice_group
otherwise                                        → text_entry
```

New detection needed:

1. A generic sibling walk for `aria-pressed` / `role=radiogroup` / `aria-checked`,
   replacing `element.closest('.ashby-application-form-input-yesno')`.
2. Group cardinality for checkboxes — how many share a group.

Everything else is already in the field dict.

---

## 6. Strategies

The eight that exist today, named, plus two that do not:

| Strategy | Operates | Source |
|---|---|---|
| `upload_file` | `file` | `set_input_files` |
| `select_by_label` | `native_select` | `option_match` → `select_option(label=)` |
| `select_by_value` | `native_select` | `select_option(value=)` — current fallback |
| `combobox_choose_from_menu` | `dynamic_choice` | `choose_combobox_option` |
| `combobox_type_and_select` | `dynamic_choice` | `select_combobox_option` |
| `click_option_button` | `choice_group` | button click + `aria-pressed` verify |
| `check_control` | `choice_group`, `toggle`, `multi_choice_group` | `control.check()` |
| `select_multiple` | `multi_choice_group` (`select[multiple]` only) | **new — does not exist** |
| `set_value` | `text_entry` | `set_registered_value` |
| `uncheck_control` | `toggle`, `multi_choice_group` | **new — does not exist** |

A control kind maps to an ordered list of candidate strategies. The first that
succeeds wins; the outcome of each attempt is recorded. Over time the ordering
is evidence-driven rather than hand-written, and a kind whose first strategy
always succeeds has reached rung 3 of the ladder.

`click_option_button` is generalised: click the button in the group whose
accessible name matches the intended value, verify via `aria-pressed`. The
Ashby `data-option` selector becomes one selector variant inside the strategy,
or disappears once the ARIA path proves sufficient.

---

## 7. Facts, prose, questions, patterns

Four entities. Facts and prose are *your answers*. A question is *what is being
asked*. A pattern *connects a short-form question to a fact*.

| Entity | What it is | Stored |
|---|---|---|
| **Fact** | Information about you: "don't require sponsorship", "Computer Science major" | base facts in `jobs_facts`; derived facts in code |
| **Prose** | Long-form writing of yours that has been saved | `jobs_prose` |
| **Question** | What is being asked, normalized, and how it was dispositioned | `jobs_questions` |
| **Pattern** | Short-form only: question → fact → formatted for this control | `jobs_patterns` + code |

Short-form and long-form are two paths that share the question entity and split
immediately after it. Short-form goes question → pattern → fact → format →
strategy (§8). Long-form goes question → nearest prose → adapt, or you write
(§7.5).

### 7.1 Facts

`jobs_facts` replaces `jobs_profile` entirely. One row per fact, keyed by a
stable dotted name. The profile's 43 columns, the library's restated values, and
the library's two regex policy rows all collapse into it.

> **R2 — `jobs_profile` is retired, not kept alongside.** Two stores for facts
> is the bug being fixed. The only argument for keeping columns is typed
> constraints (a date column rejects "Fall 2027"); `value_type` plus validation
> on write in FastAPI gives the same guarantee without a migration per new fact.
> The calculations that need graduation date or the co-op anchor read them by
> key like anything else.

> **R3 — Values are `jsonb`, typed by `value_type`.** Most facts are scalars,
> but some are not: race/ethnicity is a list, minimum salary is
> `{amount, currency, period}`, and the location strategy (§9) is a nested
> object. One `jsonb` column with a declared type covers all of them; a
> per-type column set would be mostly null.

> **R4 — Facts carry a `sensitive` flag.** ~~EEO answers, phone, and address are
> withheld from every model call: the model sees that the fact exists and its
> label, never its value. A sensitive fact still fills deterministically.~~
> **Reversed 2026-10-16.** No fact in Jobs is hidden from Jobs. The flag only
> masked a value in the worker's own terminal and withheld it from the worker's
> own model calls, which made a fill harder to read back and a failure harder to
> diagnose. The column, the flag, and the masking are gone; every fact reaches
> every model call with its value.

> **R5 — Authorization and sponsorship are two facts, not one fact with
> negation.** "Are you authorized to work in the US?" → Yes and "Will you
> require sponsorship?" → No come from `work_auth.authorized_us = true` and
> `work_auth.requires_sponsorship = false`. Storing one and negating it makes
> every pattern carry polarity logic, and the two facts are genuinely
> independent for many people.

**Validation happens in the database.** The fact catalog is seeded, then
verified by you, row by row. It is never inferred silently.

1. Seed `jobs_facts` from every `jobs_profile` column, each row unverified.
2. `os hunt facts review` walks the unverified rows. For each it shows the
   seeded value and every conflicting value the library restates ("graduation:
   profile says 2027-12-15; library also says May 2028, Fall 2027"), and you
   accept, edit, or delete it. Accepting sets `verified_at`.
3. Once every row is verified, `jobs_profile` is retired.

> **R6 — Fact values are reviewed in the database, not in a file.** The keys and
> types belong in this spec (§7.2); the values belong only in `jobs_facts`, so
> phone, address, and EEO answers never touch the repository or a local file.
> `verified_at` records the review on the row itself. Unverified rows still
> fill exactly as the profile does today, so seeding does not regress applying
> while the review is in progress. *Changed by you: originally an untracked
> local file.*

### 7.2 Draft fact catalog

Keys and types only. Values are reviewed in the database (R6). *Derived*
entries are computed in code and never stored. Entries marked *career* are
derived from the career record (§7.6) rather than stored as facts.
Until slice B builds the career record, the *career* entries are seeded and
stored as ordinary facts; B moves them out.

| Key | Type | Notes |
|---|---|---|
| `identity.legal_name` | text | |
| `identity.preferred_name` | text | |
| `identity.adult` | boolean | currently a builtin rule |
| `contact.email` | text | also the resume header |
| `contact.phone` | text | also the resume header |
| `contact.linkedin_url` / `github_url` / `portfolio_url` | text | also the resume header |
| `address.line_1` / `line_2` / `city` / `region` / `postal_code` / `country` | text | `city`, `region`, `country` answer "current location" |
| `education.school_name` / `degree_type` / `field_of_study` | *career* | from the primary education row |
| `education.graduation_date` | *career* | base for every graduation rendering |
| `education.gpa` | *career* | |
| `education.graduation_term` | *derived* | from `graduation_date` — December → Fall |
| `education.currently_enrolled` | *derived* | today < `graduation_date` |
| `education.year_in_school` | *derived* | from `graduation_date` and today |
| `coop.anchor` | object | `{count, as_of}` — §7.3 |
| `coop.completed_rotations` | *derived* | §7.3 |
| `work_auth.us_citizen` | boolean | |
| `work_auth.authorized_us` | boolean | R5 |
| `work_auth.requires_sponsorship` | boolean | R5 |
| `availability.earliest_start_date` | date | |
| `availability.notice_period_weeks` | number | replaces parsing `earliest_start_text` |
| `availability.onsite` | boolean | from the library pattern row "available to work on-site" |
| `preferences.willing_to_relocate` / `willing_to_travel` | boolean | |
| `preferences.remote` | choice | |
| `preferences.location_strategy` | object | §9 |
| `compensation.minimum` | object | `{amount: 40, currency: USD, period: hour}`; R7 |
| `compensation.minimum_annual` | *derived* | hourly × 2080 = $83,200 |
| `experience.years_professional` | *career* | see R8 |
| `experience.prior_internship` | *derived* | `coop.completed_rotations > 0` |
| `employment.worked_for` | *career* | experience organizations, given the posting's company |
| `referral.status` | choice | default `none` |
| `eeo.gender_identity` / `transgender` / `race_ethnicities` / `hispanic_latino` / `veteran_status` / `disability_status` | text, boolean, list | ordinary saved answers |
| `discovery.target_roles` / `target_locations` / `target_career_stages` | list | not application facts; used by discovery |

Dropped: `completed_at` (a form-completion marker, not a fact),
`professional_summary` (prose — moves to `jobs_prose`), `graduation_term` and
`completed_coop_terms` as stored values (now derived), `earliest_start_text`
(replaced by a date plus `notice_period_weeks`), `work_authorization_status`
and `visa_status` free text (replaced by the R5 booleans and
`work_auth.us_citizen`).

> **R7 — Minimum compensation is $40/hour, and every salary question gets a
> number.** The library's "I'm open to discussing…" rows are dropped. Hourly
> questions get `$40/hour` (or `40` in a numeric field); annual questions get the
> derived `$83,200` (or `83200`). *Changed by you: the profile's $35 becomes
> $40, and the disclosure-policy fact is not needed.*

> **R8 — `experience.years_professional` is derived from the career record.**
> The backend bootstrap seeds it as `3` when empty (`database.py:80`). With
> structured experience dates (§7.6) it is computed instead: the union of
> experience spans, in whole years, rounded down. The seeded `3` goes away.

### 7.3 Derived facts: co-op rotations

Rotations are assumed three per year — spring, summer, fall — each complete
once its boundary is reached:

| Rotation | Complete on or after |
|---|---|
| spring | May 1 |
| summer | August 1 |
| fall | November 1 |

Base fact `coop.anchor = {count: 4, as_of: 2026-08-01}`: four complete as of the
end of summer 2026. Derived:

```
completed_rotations(today) =
    anchor.count + boundaries in (anchor.as_of, min(today, graduation_date)]
```

| Date | Completed |
|---|---|
| now (2026-10-08) | 4 |
| 2026-11-01 | 5 |
| 2027-05-01 | 6 |
| 2027-08-01 | 7 |
| 2027-11-01 | 8, and stays 8 after graduation |

Always a whole number. No partial rotation is ever reported.

The anchor stays even once experience has real dates (§7.6): the rotation count
is your rule, not a sum of job spans, and the two are allowed to differ.

Derived facts live in one module (`jobs/facts/derived.py`), each declaring its
key, the base facts or career rows it reads, and whether it needs today's date
or the posting. They are listed in `os hunt facts list` beside base facts,
marked *derived*, so there is one place to see everything the system will say
about you.

### 7.4 Questions

A question is a normalized label (the existing `normalize_question`), recorded
once per distinct normalized key, with a disposition:

| Disposition | Meaning | Filled by |
|---|---|---|
| `unbound` | seen, not yet dispositioned | the model pass or you; it is an Inbox item |
| `pattern` | short-form, bound to a pattern | the pattern |
| `one_off` | answered for one application only | you, each time |
| `long_form` | wants prose | the prose path |

A question does not need a row before a pattern can answer it: regex patterns
match any phrasing at fill time, and the match is recorded on the fill event.
Every question met gets a row anyway (R44), `unbound` until someone dispositions
it, so the Inbox is a view over rows and dispositions stick.

**New wording vs genuinely new.** An unmatched question is one of two things:

| Case | Example | Fact exists? |
|---|---|---|
| new wording | "Will Acme need to file a visa petition on your behalf?" | yes |
| genuinely new | "Do you hold an active security clearance?" | no |

The existing per-page model pass tells them apart without a new call site. Its
output gains an explicit verdict per question:

- `fits_fact` — names the fact used; the fill happens, and a pattern binding is
  proposed to the Inbox
- `no_fact` — names the fact that *would* answer it ("security clearance
  status"); nothing is filled
- `unsure` — nothing is filled

> **R9 — `no_fact` must name the missing fact.** "I don't know" is not
> actionable; "this needs a security-clearance fact" is a pre-filled Inbox item
> where you supply only the value.

**Inbox resolution** for an unbound question — you pick one:

1. **Bind to an existing fact** — new wording. Usually pre-proposed.
2. **New fact, then bind** — genuinely new. Every future phrasing resolves.
3. **One-off** — the answer is kept with this application only.
4. **Long-form** — save as prose instead.

**One-offs are stored, and recurrence promotes them.** A one-off answer stays in
the application's answer history with its question. When a later question
matches an earlier one-off, the Inbox shows both answers and proposes promoting
them to a fact plus binding: seeing it twice is the evidence that it is not
really one-off.

> **R10 — Promotion triggers on the second sighting.** Day 1, "matches" means
> the same normalized key. Once question embeddings exist (§7.5), it means
> similarity above the same threshold the long-form path uses.

### 7.5 Long-form: prose matching

`jobs_prose` holds long-form writing, each piece linked to the question it was
written for. A new long-form question is matched against it:

- **at or above the threshold** — the existing textarea drafting call adapts
  the nearest prose (change a sentence, or combine two)
- **below the threshold** — nothing is generated; you write it, and it is saved
  to prose

**End state: vectors.** Embed every question that has prose, embed the incoming
question, and compare. The same index serves one-off promotion (R10).

> **R11 — Match question to question, not question to prose.** Two phrasings of
> "why this company" are far closer in embedding space than a question is to an
> essay that answers it. Compare the incoming question with the questions your
> prose was written for, and fall back to question-to-prose only for prose
> saved without its question.

> **R12 — Day 1 needs no embeddings.** The textarea drafting call — already one
> of the three permitted model call sites — is handed the candidate prose and
> asked to adapt the closest or report `none_fits`. That gives the
> above/below decision with no new call site. Vectors replace the candidate
> selection later without changing anything around it.

> **R13 — Embeddings, when built, run locally.** A small sentence-transformer
> in the worker (CPU, no API, deterministic output for a given model version)
> with pgvector storage in the existing database. This still needs AGENTS.md's
> model-call rule amended to admit embeddings, and that amendment lands with
> the feature, not before.

### 7.6 Career record: the resume in the database

Everything in `artifacts/career/main.json` moves into the database as
structured rows. Facts that the resume and the forms both need — graduation,
GPA, degree, prior employers, tenure — are then derived from one place instead
of being typed into the profile, the library, and the resume separately.

| Resume section today | Becomes |
|---|---|
| `contact` | facts (`contact.*`, `work_auth.us_citizen`) — the resume header reads them |
| `education` | a `jobs_education` row, `is_primary` |
| `experience`, `research`, `projects` | `jobs_resume_entries` rows, by `section` |
| each entry's `bullets` | `jobs_resume_bullets` rows, ordered |
| `skills` | `jobs_skills` rows, ordered |
| `meta` | dropped; `updatedAt` is the latest row update |

**Dates become dates.** `"May 2025 – Present"` is stored as `start_date =
2025-05-01`, `end_date = null`; the display string is rendered. A project with a
single month stores it as `end_date`. Month precision is enough everywhere a
resume or a form asks.

**Display text is rendered, never stored twice.** "Middleware Engineer (Co-op)"
is `title = Middleware Engineer` plus `employment_type = coop`; "BS Computer
Science" is `degree_type = BS` plus `field_of_study = Computer Science`;
"December 2027" is `graduation_date`. A form asking for the degree type gets
`degree_type` through a formatter ("Bachelor's", "Bachelor of Science"), so the
resume and the form can never disagree.

**Every consumer keeps the shape it has.** Resume build, the cover letter, the
model passes, and the pattern `RuleContext` all read a resume dict today. The
API assembles that same dict from the rows, so none of them change in this
slice. Only where the dict comes from changes.

**Editing stays a text-editor workflow.**

- `os hunt resume edit` exports the record to a temporary JSON file in the
  familiar shape, opens your editor, and on each save validates it, writes the
  rows in one transaction, and rebuilds the PDF. It replaces `resume watch`.
- `os hunt resume build` renders from the database.

**The resume rules are enforced on write.** FastAPI rejects a bullet containing
a semicolon or keyword emphasis. The two-rendered-lines rule depends on layout,
so the build checks it and names the offending bullet.

> **R32 — `main.json` and the PDF remain as generated exports; the JSON is
> committed.** `artifacts/` exists to keep a portable, versioned copy of your
> career documents, and git history of the resume is worth keeping. Build writes
> both; neither is ever read back as a source, so there is still exactly one
> writable copy — the database. AGENTS.md's resume rule is amended in the same
> change: the career record is canonical; `main.json` and `Alex_Example.pdf`
> are its generated exports. *Changed by you 2026-10-14: the PDF is ignored by
> Git, so only the JSON export is versioned.*

> **R33 — Experience entries carry `employment_type`.** `coop`, `contract`,
> `founder`, `internship`, `full_time`, `part_time`; null for research and
> projects. It drives the rendered title suffix and the derived facts:
> `experience.years_professional` (R8) is the union of experience spans of any
> employment type, which today gives 2 years (May–Dec 2023, then May 2025 to
> now). Research and projects do not count toward it. Dispute if research
> should.

> **R34 — Tailored resume variants are out of scope.** `meta.clonedFrom` hints
> at per-company copies; there is one resume today and AGENTS.md forbids a
> second writable copy. If tailoring returns, it selects and orders bullets from
> the one record rather than copying it.

---

## 8. The fill pipeline (short-form)

```
read field ─► question (label) + control (kind) + options
     │
     ▼
pattern: question ─► fact          no pattern ─► model pass ─► you
     │                                              (fits_fact / no_fact / unsure)
     ▼
format: fact × question wording × control kind × options
     │                             nothing fits ─► format failure
     ▼
strategy: operate the control      readback fails ─► control failure
     │
     ▼
filled
```

**Pattern** — matches the question and selects the fact (base or derived). A
pattern either matches by expression (builtin or condensed) or is a binding
from a specific question to a fact key (an approved Inbox decision).

**Format** — where pattern and control meet. Inputs: the fact value, its
`value_type`, the question's wording, the control kind, and the visible options.
For a choice control it selects exactly one visible option; for text it renders
the value ("Fall 2027" for a term question, "December 2027" for a month/year
question, `2027-12-15` for a date input). Formatters are keyed by `value_type`
and named like strategies (`date_as_term`, `date_as_month_year`,
`boolean_as_option`, `money_as_hourly`, …), so a format failure is attributable.

**Strategy** — operates the control (§6) and verifies by readback.

Each fill event records the stage it reached:

| Stopped at | Means | Fix goes to |
|---|---|---|
| `pattern` | nothing knows what this question asks | new binding or fact — Inbox |
| `format` | the right fact cannot be expressed in this widget or these options | a formatter — Repair |
| `strategy` | the value was right but the widget did not take it | a strategy — Repair |
| — | filled and read back | nothing |

> **R14 — `resolve_priority_answer` is split, not extended.** Its salary,
> graduation, co-op, and notice-period branches each become a derived fact plus
> a formatter. Its remaining branches (if any) become ordinary patterns. This is
> the refactor that removes the per-phrasing copies for good.

**Resolution order** stays the shape it is today, with facts in place of the
profile and the library: active question binding → active expression patterns
(exactly one match wins; zero or several pass through) → model pass → you. The
archive is never a tier (R54). Only `active` patterns are read; a `candidate`
waits in the Inbox (R43), so the same question keeps reaching the model until
you accept what it proposed.

---

## 9. Location and office selection

A question that asks you to choose an office or work location from visible
options follows `preferences.location_strategy`, a fact:

```json
{
  "tier_1_preferred_cities": [
    { "city": "Chicago", "state": "IL" },
    { "city": "Atlanta", "state": "GA" },
    { "city": "New York", "state": "NY" },
    { "city": "San Francisco", "state": "CA" },
    { "city": "Seattle", "state": "WA" }
  ],
  "tier_2_state_fallbacks": ["IL", "GA", "NY", "CA"],
  "tier_3_region_fallbacks": {
    "preferred_region": "US-East",
    "acceptable_regions": ["US-Midwest", "US-East", "US-West"]
  }
}
```

Selection, by the builtin pattern `builtin:office_selection_v1`:

1. Options matching a tier 1 city — pick one at random if several.
2. Otherwise options in a tier 2 state — pick one at random.
3. Otherwise options in an acceptable region — US-East first, then the others,
   at random within the first region that has any.
4. Otherwise, if the field is required: a random US-East or Eastern Time option,
   else the first valid option.

Random draws are seeded by the application id (§4).

> **R15 — This applies to office *choice*, never to current location.** "Where
> are you currently located?" is `address.city` + `address.state`. "Which
> office would you like to work from?" is this policy. The pattern matches only
> choice questions about preferred, desired, or available offices and locations,
> and only when options are visible.

> **R16 — Options are parsed into (city, state, region) in code.** A fixed table
> maps state codes and names to regions and to Eastern Time, and common forms
> ("NYC", "New York, NY", "Manhattan", "SF Bay Area") normalize to a city.
> Proposed regions: US-East = Northeast and Southeast Atlantic states
> (ME–FL along the coast, plus GA, VT, WV); US-Midwest = the census Midwest;
> US-West = the Pacific and Mountain states. An option that cannot be parsed is
> treated as non-US.

> **R17 — Texas and the South Central states stay outside every tier.** Austin
> and Dallas are reachable only through step 4. *Decided by you.*

> **R18 — Washington counts only as Seattle.** `WA` is deliberately absent from
> tier 2; a Redmond or Bellevue office falls to tier 3 (US-West). *Decided by
> you.*

> **R19 — "Remote" ranks after tier 3 and before step 4.** It is chosen only
> when no preferred or acceptable office is offered. Change to tier 1 if you
> would rather take remote over any office.

> **R20 — Multi-location questions.** *Decided by you.* Three shapes:
>
> - **Select all that apply, no limit** — every tier 1 and tier 2 match is
>   selected. If none match, it falls back to the single-choice steps.
> - **Select up to *k*** — tier 1 matches in random order fill the slots; once
>   tier 1 is exhausted, tier 2 matches in random order fill the rest.
> - **Typed entries** (several "Preferred location" text or typeahead slots) —
>   tier 1 cities only, in random order. Running out of tier 1 cities before
>   running out of slots is fine; extra slots stay empty unless required, in
>   which case step 4 applies.
>
> All draws use the per-application seed.

---

## 10. The application path

### 10.1 One application

```
hunt apply
  └─► open browser + application
        └─► read page
              ├─► short-form: pattern ─► format ─► strategy   (§8)
              │      ├─ filled + readback ok ─────────────────────────┐
              │      └─ any failure ─► fill_event(stopped_at)        │
              │             ├─► enqueue repair (§10.3, --repair)      │
              │             └─► field handed to you ─► you fill it ───┤
              │                                                        │
              ├─► long-form: nearest prose                            │
              │      ├─ above threshold ─► adapt ─────────────────────┤
              │      └─ below ─► you write ─► saved to prose ─────────┤
              │                                                        ▼
              ├─► cover letter offered? ─► always attached (§10.2)    │
              │                                                        ▼
              └─► page gate: every field filled? ── no ─► wait for you
                                    │ yes
                                    ▼
                         next page … ─► Audit (sampled) ─► submit
```

### 10.2 Every field is filled

*As built 2026-10-14:* you own Next and Submit, so the worker cannot hold a page;
the gate is that every empty field, required or optional, is outlined on the page
and listed with why it is empty, and what you type is captured as `manual`.

The run never advances past a page with a field empty, required or optional.
The full order for any field is: pattern → model pass → you. When it reaches
you, the run waits on the page, highlights the field in the visible browser, and
continues once readback shows a value. What you type is captured as a `manual`
fill, which is exactly the evidence the Inbox and Condenser need.

> **R21 — Optional fields are filled like required ones.** *Changed by you:
> originally optional fields never blocked.* The one exception is a field whose
> true answer is empty, and only when a fact says so — no second address line,
> no referrer when `referral.status = none`. That is recorded as a deliberate
> empty fill (`source = pattern`, empty value hash), so "left blank on purpose"
> and "could not fill" are distinguishable in the metrics.

**The cover letter is always attached.** Any cover letter field —
`is_cover_letter_field` already detects upload and text variants — is filled
whether or not it is required. An upload gets the generated PDF; a text field
gets the same letter as text. The page gate treats a cover letter field like
any other: it does not pass empty.

> **R35 — With `--no-claude`, the cover letter waits for you.** The letter's
> tailoring slots are one of the three model call sites, so `--no-claude` cannot
> produce it. Rather than attach an untailored template, the gate hands the field
> to you. Dispute if an untailored letter is better than a pause.

**The audit repairs what it finds.** *Added 2026-10-16.* Listing why a field is
empty is not a gate, it is a report. After every tier has had its turn on a page
step, the audit classifies what is still wrong and runs the matching lane, up to
two rounds, then reports only what no lane could fix:

| cause | evidence | repair |
|---|---|---|
| `fact_gap` | a `pattern` miss, or a `no_fact` verdict naming the fact | `--ask-facts` asks for it and fills it in; otherwise reported |
| `pattern_gap` | no binding, no expression, nothing matched | put back to the model with the control's options |
| `format_error` | a `format` miss, or a format failure | the fact's other renderings (§10.3) |
| `control_error` | a failure that stopped at `strategy` | the control's other strategies (§10.3) |
| `wrong_answer` | a fact contradicts what the page holds | re-render that fact |

> **R60 — Emptiness is not the only way a form is wrong.** An answer can be
> confidently filled and contradict a fact. One bounded model pass per step
> reads back every short answer and asks whether a fact says otherwise; a
> `wrong` verdict names the fact, never a value, and the correction renders that
> fact through a formatter like any other fill. *Decided by you 2026-10-16:
> accuracy is built in from the start, because the training loop has to be able
> to run unattended.*

> **R61 — A wrong answer you typed is reported, never overwritten.** The
> correction only replaces a value the worker itself produced (`pattern` or
> `model`). The only thing that outranks a fact is the person the fact is about.

> **R62 — A question reaches the model at most twice in a run.** Once in the
> fill pass, once more from the audit after a repair or a new fact changed
> something. *Changed by you 2026-10-16: it was once per run, which meant a
> transient failure cost that question the whole application.*

> **R63 — Repair is budgeted, not unbounded.** 25 operations per question and
> 150 per run, each retry costing five, so a pathological control cannot spend a
> session. A question that runs out says so.

### 10.3 Repair: fix in a second browser, behind a flag

Repair runs only when you ask for it: `os hunt apply --repair`. The flag stays
explicit while the system is being built up, so running it is a deliberate
choice; once fills are reliable you stop passing it. *Decided by you.*

| | `hunt apply` | `hunt apply --repair` |
|---|---|---|
| failure | recorded; field handed to you; run continues | same, plus enqueued for repair |
| repair worker | not started | started in the background |
| Workday and account-gated ATSs | same as any ATS | in-place mode (§10.4) |
| queue | accumulates for a later `os hunt repair` | drained while you apply |

With the flag, a format or strategy failure is enqueued and the live run carries
on. A repair worker opens the same application in a **separate browser
context**, advances to the failing step, and works on *only that field*:

1. Navigate to the application URL in the isolated context.
2. Replay earlier steps with active rules, never submitting.
3. At the failing field, try candidates in order:
   - `format` failure — alternative formatters for the value type, and option
     choosers (`choose_synonym`, `choose_numeric_range`) the pattern did not use
   - `strategy` failure — the control kind's other strategies, in precedence
     order, then the generic ARIA variants
4. A candidate that fills **and** reads back becomes a *repaired* rule.
5. If nothing works, the issue goes to the existing `diagnose → draft-fix →
   apply-fix` path, which produces a code change for you to review.

Pattern-stage failures do not go to Repair: no amount of retrying invents a
fact. They go to the Inbox (§7.4).

> **R22 — The repair browser is a separate headed context, not headless at
> first.** *Now only for `os hunt repair` (R55).* A new isolated context in the same Chrome over CDP: no shared
> cookies, no shared drafts, its own window that can sit behind yours. Headless
> changes the user agent, viewport, and font metrics — exactly the signals bot
> detection and layout-dependent widgets react to — so a headless repair would
> sometimes "fix" a control that only breaks headless, or fail on one that works
> headed. Headless is a later experiment: run both on the same failures and
> compare outcomes before switching.

> **R23 — The repair context can never submit.** The submit control is blocked
> in that context by the same guard the dry run uses, plus a hard refusal in the
> repair worker itself. Two independent guards, because a stray submit is the
> one irreversible mistake in this design.

> **R24 — Account-gated ATSs are never repaired in a second browser.** *Superseded by R55.* Workday
> and similar ATSs save drafts server-side per account; a second context editing
> the same draft would race the live run. Under `--repair` they use in-place
> mode instead (§10.4). *Decided by you.*

> **R25 — Repairs run in one background process started by `hunt apply
> --repair`.** *Superseded by R55.* One worker, one repair at a time, consuming the queue while you
> apply. It also runs standalone (`os hunt repair`) to drain what accumulated
> without the flag. Its progress shows as a single status line in the apply
> session, so it never interleaves with prompts that need you.

### 10.4 In-place mode: Workday as the training ground

*Superseded by R55: `--repair` retries a failed field on the live page right
after the step's fill, on every site, and does not hold the run on the page.
Kept for the record.*

For Workday and other account-gated ATSs, `--repair` changes the run instead of
starting a worker: **the run stays on the current page until every field on it
has a working pattern and control, then moves on.**

```
read page ─► fill every field
                │
                ├─ all filled + read back ──────────────► next page
                │
                └─ any failure ─► try candidates on this page (§10.3 step 3)
                      ├─ repaired ─► re-read page, loop
                      ├─ needs a fact / prose / your review ─► Inbox item, wait
                      └─ no candidate works ─► you fill it; issue to draft-fix
```

Workday asks many questions no other ATS asks, so a Workday application left
running on the side is the fastest way to build both a pristine Workday filler
and the fact base. It is expected to block on you often at first — mostly for
facts — and less over time.

> **R36 — In-place mode probes the live page, and that is the point.** Probing
> is otherwise forbidden on a live application (§4). A run started with
> `--repair` on an account-gated ATS has opted into being a training run, so
> probing its own current page is allowed. Submitting still requires your
> confirmation exactly as today.

> **R37 — A blocked in-place run waits on the Inbox, not on the terminal.** When
> it needs a fact, prose, or a review, it creates an Inbox item and polls for its
> resolution, resuming by itself once you answer. Because Inbox items are readable
> through FastAPI, an agent running the Workday session elsewhere — Muse in its
> VM, eventually — can see what is blocked and ping you, and you can answer from
> anywhere rather than at the machine running Chrome. Push notification itself is
> left to that agent; this system only makes the waiting state readable.

> **R38 — In-place mode needs the Inbox's read API early.** That makes a minimal
> Inbox (list blocked items, resolve one) part of slice F rather than waiting for
> the full Inbox unification. It needs no table: an Inbox item is a view over
> rows that already exist — an `unbound` question, a `repaired` rule, an open
> repair issue — so resolving one is an ordinary write to that row.

### 10.5 Rule status and `hunt validate`

A rule — a pattern binding, a formatter choice, or a strategy precedence — has a
status:

```
candidate ──► repaired ──► validated ──► active
                 │              │
                 └──► rejected  └──► retired
```

- `candidate` — proposed by the Inbox, the model pass, or the Condenser
- `repaired` — the repair worker filled and read it back
- `validated` — you watched it work and accepted it
- `active` — used by live fills
- `rejected` / `retired` — kept for the record, never used

**For now, every rule needs your validation before it is active.** A
configuration flag (`require_manual_validation`, default `true`) lets repaired
rules activate directly once enough have been validated without a rejection.

`os hunt validate` walks the repaired rules waiting on you. For each one it
opens a visible browser on an application that shows the question, advances to
the step, fills **only that field** with the candidate rule, highlights it, and
asks:

```
[validate] "Preferred office location" — native_select
  pattern  builtin:office_selection_v1   value  "Chicago, IL"
  strategy select_by_label                readback ok
  accept / reject / skip ?
```

> **R26 — Validation replays prefer a fresh posting.** Validating on the
> application where the failure happened risks touching a submitted or
> account-gated draft. `hunt validate` first looks for another open posting that
> shows the same question with the same control, and falls back to the original
> application only when it has not been submitted.

> **R27 — Existing builtin rules start `active`.** *Superseded by R40 and R46:*
> the rules in `patterns.py` are archived and re-learned; only the strategies
> stay active.

---

## 11. Metrics

One event row per fill attempt:

```
fill_event(question, pattern, fact, formatter, control, strategy, ats,
           value_hash, stopped_at, operated_ok, source, run, step, created_at)
```

`value_hash` rather than the value itself: the answer text already lives in
`jobs_application_questions`, and duplicating it here would create a second copy
to keep correct. The hash is enough to ask "same value, different outcome?".

Two distinct notions of success, deliberately separated:

- **Operational success** — the widget accepted the value; readback passed.
  Known immediately, written during the run.
- **Answer correctness** — the value was right. Known later, from an Audit
  verdict or from whether you edited it. Written as a separate verdict row
  referencing the fill event.

Rates are queries:

- control success = operated_ok / attempted, grouped by control
- pattern success = confirmed_correct / used, grouped by pattern
- pair success = both, grouped by (control, pattern) — the fill *rule*
- strategy success = operated_ok, grouped by (control, strategy)
- formatter success = reached strategy / attempted, grouped by (formatter, control kind)
- stage breakdown = attempts grouped by `stopped_at` — where the pipeline loses fields
- per-ATS breakdown = any of the above, grouped additionally by ats

The pair is the unit worth watching, because it catches interaction failures the
marginals hide: a pattern 95% correct overall and a control 98% operable overall
can still fail consistently together when the pattern emits a value the widget
cannot represent. `stopped_at = format` is that failure, named.

At current scale — about four thousand listings and low thousands of fill events
— an index on `(control_id, pattern_id)` is sufficient. Materialised rollups are
premature.

---

## 12. Schema

**Approved 2026-10-08.** Everything in this section is approved except the
entries marked **added after approval**, which arose from your answers to the
review and still need a yes before they are built.

Fields marked *derived* are not stored. Every table also carries `user_id` and
`created_at`/`updated_at` as the existing tables do; they are omitted below.

### New: `jobs_facts` (replaces `jobs_profile`)

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `key` | text | dotted name, unique per user |
| `label` | text | plain-language description shown to you and to the model |
| `value` | jsonb | R3 |
| `value_type` | text | `text` / `boolean` / `number` / `date` / `choice` / `list` / `object` |
| `verified_at` | timestamptz null | **added after approval** — R6; null until you accept the row |

### Retired: `jobs_profile`

Dropped once every seeded fact is verified (§7.1). FastAPI's profile endpoints
move to facts; `database.py`'s bootstrap defaults are removed rather than
re-seeded.

### New, added after approval: the career record (§7.6)

| `jobs_education` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `school` | text | |
| `location` | text | |
| `degree_type` | text | `BS`; formatted per form |
| `field_of_study` | text | |
| `concentrations` | text | today's `threads` |
| `coursework` | text | |
| `graduation_date` | date | |
| `gpa` | numeric | |
| `gpa_scale` | numeric | |
| `is_primary` | boolean | the row education facts derive from; one per user |
| `display_order` | integer | |

| `jobs_resume_entries` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `section` | text | `experience` / `research` / `project` |
| `title` | text | without the employment-type suffix |
| `org` | text null | |
| `location` | text null | |
| `qualifier` | text null | e.g. "Continuous Employment", an award |
| `employment_type` | text null | R33 |
| `start_date` | date null | month precision |
| `end_date` | date null | null with a start date = present |
| `display_order` | integer | |

| `jobs_resume_bullets` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `entry_id` | uuid | |
| `text` | text | no semicolons, no emphasis — checked on write |
| `display_order` | integer | |

| `jobs_skills` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `category` | text | |
| `items` | text[] | today a comma-separated string |
| `display_order` | integer | |

### New: `jobs_questions`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `normalized_key` | text | via `normalize_question`, unique per user |
| `sample_label` | text | one observed raw phrasing, for display |
| `form` | text | `short` / `long` |
| `disposition` | text | `unbound` / `pattern` / `one_off` / `long_form` |
| `pattern_id` | uuid null | set when `disposition = pattern` |

### New: `jobs_patterns`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `resolver_id` | text | stable id, unique per user: `binding:<question key>` or `expression:<name>` |
| `kind` | text | `expression` (matches any phrasing) / `binding` (one question → one fact) |
| `expression` | text null | case-insensitive regex for `expression` patterns stored as data |
| `question_id` | uuid null | **added 2026-10-14** — the question a `binding` answers; a candidate needs it before the question is bound |
| `fact_key` | text | the fact the pattern reads; every pattern is data now (R40), so never null |
| `formatter` | text null | **added 2026-10-14** — pins a formatter; null lets the fill choose (§8) |
| `origin` | text | `builtin` / `inbox` / `model` / `condensed` / `archive` (**added 2026-10-14**) |
| `archive_ref` | text null | **added 2026-10-14** — the archived rule or answer it was translated from |
| `status` | text | §10.5 |
| `superseded_by` | uuid null | set when condensed |

### New: `jobs_controls`, `jobs_strategies`

| `jobs_controls` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `kind` | text | one of the 7 in §5 |
| `attributes` | jsonb | raw observed attribute vector (§13) |
| `first_seen_at` | timestamptz | |

| `jobs_strategies` | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `name` | text | e.g. `select_by_label` |
| `control_kind` | text | the kind it operates |
| `precedence` | integer | candidate ordering within a kind |
| `status` | text | §10.5 |

### New: `jobs_fill_events`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `run_id` | uuid | |
| `step` | integer | |
| `question_id` | uuid | |
| `pattern_id` | uuid null | null = no pattern matched |
| `fact_key` | text null | which fact supplied the value |
| `formatter` | text null | named formatter, §8 |
| `control_id` | uuid | |
| `strategy_id` | uuid null | null = never attempted |
| `source` | text | `pattern` / `model` / `manual` / `prose` |
| `value_hash` | text | hash only |
| `ats` | text | dimension only, never control identity |
| `stopped_at` | text null | `pattern` / `format` / `strategy`; null = filled or not attempted |
| `operated_ok` | boolean null | true = read back; false = failed; null = not attempted (a dry run, a missing document) |

As built in slice D, `question_id` and `pattern_id` are not yet columns: their
tables belong to slice C. The event carries `question_key` (the normalized
question) and `resolver_id` (the pattern's existing stable id) instead, and
slice C's migration adds the two foreign keys and backfills them from those
columns. `jobs_controls` is unique on `(user_id, kind, attributes)`, where
`attributes` is the structural vector only.

### New: `jobs_prose`; `jobs_answer_library` → `jobs_archive_answers`

*Changed 2026-10-14 (R42).* `jobs_prose` is a new, empty table of long-form
writing: `id`, `question_id` (the question it was written for, R11), `text`,
`approved`, and `origin` (`manual` / `model` / `archive`). A long-form answer
you submit is saved to it unapproved; the Inbox approves it. The 198 library
rows are not migrated: the table is renamed `jobs_archive_answers` and frozen,
and §14 says how its rows are reached.

> **R28 — Rename the table.** Its meaning is changing; keeping the name
> `answer_library` keeps the ambiguity that started this. *Changed by R42: the
> rename goes to the archive, and prose starts as its own table.*

### Archived: `jobs_archive_profile`, `jobs_archive_documents`

*Added 2026-10-14 (R42).* `jobs_profile` and `jobs_documents` are renamed and
frozen (`select` only). The backend's bootstrap stops writing either, and the
documents endpoints, which no client calls, are removed.

### Changed: `jobs_application_questions`

Gains `question_id`, and `answer_source` gains `archive` and `prose`. It becomes
the per-application *answer* record: what was submitted for each question on
each application — which is also where one-off answers live. Its run-event columns (`run_id`, `event_index`, `event_kind`,
`step_number`, `control_kind`, `field_fingerprint`, `resolver_id`,
`proposed_value`) are superseded by `jobs_fill_events`.

> **R29 — Event columns stop being written when fill events land, and are dropped
> one release later.** Dropping them in the same migration would lose 928 rows
> of history before the new table has proven it captures the same information.

### Changed: `jobs_autofill_failure_issues` becomes the repair queue

It already has a status, a diagnosis, a proposed patch, and replay and live
verification timestamps — most of a repair record. It gains:

| Field | Type | Notes |
|---|---|---|
| `fill_event_id` | uuid | the failure that enqueued it |
| `stopped_at` | text | `format` / `strategy` |
| `candidate` | jsonb null | the repaired rule: formatter or strategy, and the value |
| `repaired_at` | timestamptz null | |
| `validated_at` | timestamptz null | replaces `live_verified_at` |

`jobs_autofill_failure_occurrences` stays as the evidence attached to each
failure (DOM signals, artifact paths, intended vs observed value). Its `status`
gains `archived` (**added 2026-10-14**, R50) for the issues the old dispatch
left open.

### Withdrawn

`review_disposition`, `review_notes`, `reviewed_at` on answer history — the
earlier pending proposal. Question dispositions (§7.4) and rule status (§10.5)
replace it.

### Later, with embeddings (not part of this approval)

`jobs_question_embeddings(question_id, model, embedding vector)` — R13.

---

## 13. Deferred evidence

Instrumentation collected now for later evaluation. Each entry records a review
trigger and the decision it informs; without both, the field is not collected.

| Collected | Question it answers later | Review trigger | Decision it informs |
|---|---|---|---|
| Raw control attribute vector | Is a kind really two kinds? | a kind's strategy success goes bimodal, or n >= 50 observations | split `dynamic_choice` into local-filter vs server-search |
| Typeahead behavior — does a menu appear on keystroke | Which combobox subkinds need which strategy | first repeated failure of a `dynamic_choice` strategy | add a strategy, or split the kind |
| `aria-*` state attributes beyond those dispatched on | Which generic signals predict operability | before adding any new ATS-specific selector | replace a hardcoded selector with a generic one |
| Audit verdicts per pair | Which patterns are wrong vs which controls are broken | Condenser build | pattern merge or retire |
| Model-pass `no_fact` names | Which facts are missing most often | 20 `no_fact` verdicts | which facts to ask you for first |
| Prose `none_fits` verdicts with nearest candidate | Where the similarity threshold sits | 30 long-form questions | the threshold value, and whether vectors are needed |

Proposed AGENTS.md convention: *instrumentation collected for later evaluation
records its review trigger and the decision it informs, or it is not collected.*

---

## 14. The archive

*Rewritten 2026-10-15 (R54).* Nothing is migrated up front, and nothing in an
application reads the archive. It serves the facts session: every retired rule
— the 26 builtins, the priority policies, the exact-label map, the two library
regex rows — keeps its regular expression and its translation (the fact key it
reads and, where the wording decides it, a formatter), so `os hunt archive` can
show each old question beside the fact it asked about and what you answered,
and `os hunt facts review` can show what the old answers said beside each
unverified fact. Once the facts session has settled every fact, the archive
tables and `jobs/archive/` are dropped.

A question with no active pattern goes to the model pass and then to you; what
you bind in the Inbox is what fills it next time.

> **R30 — Every archived phrasing becomes a regression test.** *Changed by R40; dropped with the archive (R54).*
> The archived questions that translate to a fact — the ~25 sponsorship
> phrasings, the graduation phrasings, and the rest — are the test corpus for
> the archive's translation: each must name the same fact key it did before. The
> corpus holds questions and fact keys only, never the old values.

---

## 15. Gaps this surfaces

- **No `uncheck`.** `.check()` is the only check operation. Resets and repair
  replays cannot clear a checkbox set by a prior pass.
- **`select[multiple]` is unhandled.**
- **Radio, lone checkbox, and checkbox group are one branch**, so three distinct
  kinds share one strategy and one undifferentiated failure mode.
- **Generalising the captcha guard changes behavior.** Dropping `ats == "lever"`
  makes it fire on every ATS. As built, it ignores invisible-mode reCAPTCHA (the
  corner badge, `data-size="invisible"`, `size=invisible` frames): taken
  literally, the presence test would have parked every choice on boards that
  carry only that badge. A refusal is recorded as `stopped_at = strategy` with
  failure category `captcha_guard`, so "refused on purpose" stays distinguishable.
- **AGENTS.md's model-call list was incomplete.** It named three call sites, but
  `failures diagnose` and `draft-fix` call `claude -p` too (`_failure_claude`,
  `commands.py:615`), and Repair leans on that path.
- **`_failure_claude` is not metered.** Every other call site runs
  `--output-format json` and reports usage through `claude_usage`; this one
  reads plain text, so its cost is invisible.

> **R31 — Every model call site is listed in AGENTS.md, by file and purpose.**
> *Done, by your decision.* Fill-time calls (the three `--no-claude` disables)
> and maintenance calls (run by you on a captured failure, never inside a live
> fill) are listed separately, plus the parked graduation-date call. Adding a
> call site means adding it to that list in the same change.

> **R39 — Meter `_failure_claude` like every other call.** Switch it to
> `--output-format json` and route it through `claude_usage`, so the list in
> AGENTS.md and the usage records cover the same set of callers. Part of slice
> F, since Repair is what makes that path frequent.

---

## 16. Slices

The work is large and lands as six slices. Each slice owns its migration, its
modules, and its tests, so independent slices can be built at the same time
without editing each other's files.

**Separation rules.**

- One migration file per slice. A slice never edits another slice's migration;
  a later correction is a new migration in the slice that needs it.
- New backend storage goes in a new module per slice (`jobs_facts.py`,
  `jobs_career.py`, `jobs_registry.py`) rather than growing `jobs_store.py`.
- Hot files have one owner at a time: `browser.py` belongs to D;
  `autofill.py` resolution and `patterns.py` belong to C until C lands, then E.
- Each slice ends with the verification gates in AGENTS.md green, its owning
  README updated, and a commit on `main`.

```
A Facts ──┬──► B Career record
          ├──► C Answers ──┐
          │                ▼
D Registry ──────────────► E Fill policy ──► F Repair and validate
```

Lanes that can run at once: **A → B**, **A → C**, and **D** from the start. E
waits for A, C, and D; F waits for D and E.

### A — Facts

| | |
|---|---|
| **Status** | built 2026-10-08 |
| **Delivers** | `jobs_facts`, seeded from `jobs_profile`; derived facts including co-op rotations; `os hunt facts list \| review \| set` |
| **Owns** | migration `…_jobs_facts.sql`; `backend/…/jobs_facts.py`; `workers/…/jobs/facts/` |
| **Cut-over** | the worker reads facts through an adapter that presents the old profile dict, so resolution logic is untouched until E |
| **Done when** | every catalog key listed; co-op derivation tested at each boundary date and at graduation; a dry run over saved fixtures produces the same fill plan as before |
| **Then** | `jobs_profile` is dropped in a follow-up migration once every fact is verified (A2) |

### B — Career record

| | |
|---|---|
| **Status** | built 2026-10-14 |
| **Delivers** | the four career tables; a one-time `os hunt resume import` from `main.json`; build and `resume edit` from the database; career-derived facts (shadowed by stored ones, R51); the AGENTS.md resume rule amendment (R32); `jobs_documents` archived and the bootstrap seeding removed (R42) |
| **Owns** | migration `…_jobs_career.sql`; `backend/…/jobs_career.py`; `workers/…/documents/` |
| **Depends on** | A, for the `contact.*` facts in the header |
| **Done when** | the PDF built from the database matches the current PDF; export after import round-trips to the current `main.json` except the dropped `meta` |

### C — Answers

| | |
|---|---|
| **Status** | built 2026-10-14 |
| **Delivers** | *Reshaped 2026-10-14.* The archive (R42): the library and profile renamed and frozen, old failures archived (R50), the retired rules in `jobs/archive/` with their translations; `jobs_questions` recorded on encounter (R44), `jobs_patterns`, a new empty `jobs_prose`, `question_id` on answer history and fill events; the fact catalog (§7.2) as code; `os hunt inbox` (R47), `os hunt patterns`, `os hunt prose`, `os hunt archive`; the regression corpus (R30) |
| **Owns** | migration `…_jobs_answers.sql`; `backend/…/jobs_answers.py`; `workers/…/jobs/archive/`, `jobs/inbox.py`, `jobs/facts/catalog.py` |
| **Depends on** | A |
| **Done when** | every archived phrasing in the corpus translates to its fact key; the API role can only read the archive tables; an accepted candidate is read as `active` by the next run |

### D — Registry instrumentation

| | |
|---|---|
| **Status** | built 2026-10-08 |
| **Delivers** | `jobs_controls`, `jobs_strategies`, `jobs_fill_events`; classification into the 7 kinds; generic ARIA detection; checkbox group cardinality; the dispatch refactored to kind → ordered strategies; `uncheck_control`, `select_multiple`; the generic captcha guard; fill events written with `stopped_at` |
| **Owns** | migration `…_jobs_registry.sql`; `backend/…/jobs_registry.py`; `workers/…/jobs/browser.py` and a new `jobs/registry/` |
| **Depends on** | nothing |
| **Done when** | replay fixtures exist for every control kind; a dry run writes one fill event per field; existing tests pass unchanged |

### E — Fill policy

| | |
|---|---|
| **Status** | built 2026-10-14 |
| **Delivers** | resolution from active patterns, then the model (the archive tier was removed by R54), always through a fact and a formatter (§8, §14); formatters; the `resolve_priority_answer` split (R14), its old branches archived; office selection (§9); the every-field page gate (§10.2); the cover letter always attached; model-pass verdicts `fits_fact` / `no_fact` / `unsure`; $40/hour |
| **Owns** | `workers/…/jobs/formatters.py`, `jobs/office.py`; `autofill.py`, `autofill_session.py`, `short_answers.py` |
| **Depends on** | A, C, D |
| **Done when** | formatter tests cover every `value_type` × control kind pair in use; office selection is tested for every tier and multi-location shape with a fixed seed; a fixture page with an empty optional field does not advance |

### F — Repair and validate

| | |
|---|---|
| **Status** | built 2026-10-14; repair made in place 2026-10-15 (R55) |
| **Delivers** | `--repair`, retrying in place on the live page (R55); the isolated context for `os hunt repair`; rule status; `os hunt repair`; `os hunt validate`; the minimal Inbox read API (R38); metering for `_failure_claude` (R39) |
| **Owns** | migration `…_jobs_repair.sql`; `workers/…/jobs/repair/`; `commands.py` failure path |
| **Depends on** | D, E |
| **Done when** | a seeded strategy failure is repaired and validated end to end on a fixture; the repair context's submit is refused by both guards; an in-place run blocks on a missing fact and resumes when it is set |

### Later

Audit, Condenser, prose vectors, unattended batch; dropping the archive after the facts session (R54).

### Not doing

Vision or computer-use fallback for control operation; CAPTCHA solving; any port
off the `claude -p` CLI; tailored resume variants (R34).

---

## 17. Recommendations index

*delegated* = decided by Claude on 2026-10-14 under your instruction to proceed
on its own judgement and report the decisions; dispute any by number.

| # | Decision | Status |
|---|---|---|
| R1 | Build Facts first; Repair before Audit; Registry in parallel | agreed |
| R2 | Retire `jobs_profile` entirely | agreed |
| R3 | Fact values are `jsonb` typed by `value_type` | agreed |
| R4 | `sensitive` facts are withheld from model calls | **reversed 2026-10-16** — no fact is hidden from Jobs |
| R5 | Authorization and sponsorship are separate facts | agreed |
| R60 | The audit checks filled answers against the facts, not only emptiness | decided by you 2026-10-16 |
| R61 | A wrong answer you typed is reported, never overwritten | decided by you 2026-10-16 |
| R62 | A question reaches the model at most twice in a run | changed by you 2026-10-16 |
| R63 | Repair is budgeted: 25 operations a question, 150 a run | decided by you 2026-10-16 |
| R6 | Fact values reviewed in the database, `verified_at` per row | changed by you |
| R7 | Minimum $40/hour; annual derived; always a number | changed by you |
| R8 | `years_professional` derived from the career record | changed by your resume decision |
| R9 | `no_fact` names the missing fact | agreed |
| R10 | One-offs promote on the second sighting | agreed |
| R11 | Long-form matches question to question | agreed |
| R12 | Day 1 long-form matching uses the existing drafting call | agreed |
| R13 | Embeddings run locally, with an AGENTS.md amendment | agreed |
| R14 | Split `resolve_priority_answer` into derived facts and formatters | agreed |
| R15 | Office policy applies to office choice, never current location | agreed |
| R16 | Option parsing and region table in code | agreed |
| R17 | Texas / South Central stay outside every tier | decided by you |
| R18 | Washington counts only as Seattle | decided by you |
| R19 | "Remote" ranks after tier 3 | agreed |
| R20 | Multi-location: all of tiers 1–2; limited slots tier 1 then 2; typed slots tier 1 only | decided by you |
| R21 | Optional fields are filled like required ones | changed by you |
| R22 | Repair runs in a separate headed context, headless later | agreed; `os hunt repair` only (R55) |
| R23 | Two independent submit guards in Repair | agreed |
| R24 | Account-gated ATSs use in-place mode, never a second browser | superseded by R55 |
| R25 | One background repair worker, only under `--repair` | superseded by R55 |
| R26 | Validation prefers a fresh posting | agreed |
| R27 | Existing builtin rules start `active` | superseded by R40, R46 |
| R28 | Rename `jobs_answer_library` (now to `jobs_archive_answers`) | changed by R42 |
| R29 | Event columns dropped one release after fill events land | agreed |
| R30 | Every archived phrasing becomes a translation test | changed by R40 |
| R31 | Every model call site listed in AGENTS.md | done |
| R32 | `main.json` stays as a committed generated export; the PDF is generated and Git-ignored (your 2026-10-14 change) | delegated |
| R33 | Experience entries carry `employment_type`; research does not count toward tenure | delegated |
| R34 | Tailored resume variants out of scope | delegated |
| R35 | With `--no-claude`, the cover letter waits for you | delegated |
| R36 | In-place mode may probe its own current page | delegated |
| R37 | A blocked in-place run waits on the Inbox, readable over the API | delegated |
| R38 | Minimal Inbox read API, no table (moved to slice C by R47) | delegated |
| R39 | Meter `_failure_claude` | delegated |
| R40 | No pattern is active on day one; old rules archived | delegated (your direction) |
| R41 | The archive answers which fact, never what value | delegated; fill-time half superseded by R54 |
| R42 | Archive in place, frozen; rules move to `jobs/archive/` | delegated |
| R43 | Archive and model fills propose candidates; you activate | delegated; model only (R54) |
| R44 | Questions recorded when they are met | delegated |
| R45 | Engine rules stay active and are not patterns | delegated |
| R46 | Strategies stay active; controls learned by observation | delegated |
| R47 | `os hunt inbox` ships in slice C | delegated |
| R48 | Accepting a candidate activates it | delegated |
| R49 | An archived rule's expression is adopted on request | superseded by R54 |
| R50 | Old failures archived; stale runs abandoned | delegated |
| R51 | Stored facts shadow career-derived ones until removed | delegated |
| R52 | In-place mode waits for an account-gated adapter | superseded by R53 |
| R53 | No adapters; every site filled from its controls; `ats` is a label | decided by you |
| R54 | The archive is for the facts session only, then dropped | decided by you |
| R55 | Repair runs in place on the live page under `--repair` | decided by you; flag dropped by R56 |
| R56 | Repair in place is on for every application; `--no-repair` turns it off | decided by you |
| R57 | Apply continues on the tab already open for this application; `--fresh` opens a new one | decided by you |
| R58 | A gate between the posting and its form is a choice control answered by a fact | decided by you |
| R59 | The Inbox widens a binding into an expression, so expressions are creatable | found in review |

## 18. Open questions

Only the things a recommendation cannot settle:

1. The fact values themselves — settled in the facts session with you (§0),
   including which stored facts give way to career-derived ones (R51).
2. The long-form similarity threshold — evidence collected per §13 before it is
   fixed.
3. Audit sampling rates and the bound that keeps it off the critical path.
