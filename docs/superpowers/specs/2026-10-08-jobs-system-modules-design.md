# Jobs system: modules, registry, and the application path

Date: 2026-10-08. Status: proposed, awaiting review and schema sign-off.

This document describes the Jobs system as a set of modules with explicit
boundaries, and specifies them in enough detail to build. It replaces the
implicit structure that grew inside `workers/` with named parts, measurable
rules, and a path that gets more deterministic over time.

Nothing here is built. Every schema change in §12 needs explicit confirmation
before implementation, per AGENTS.md.

**Reading the recommendations.** Where the design was unclear, a decision has
been taken and marked as a numbered recommendation:

> **R0 — Example.** The decision, then why.

Each is a default, not a question. Disagree with one by number; anything not
disputed is treated as agreed. §17 indexes them all.

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

- `jobs_profile` — superseded by `jobs_facts` (§7).
- Short-form rows in `jobs_answer_library` — become facts, pattern bindings, or
  one-off answers (§7, §14).
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
| **Facts** | Base facts about you, derived facts computed from them | what is true about you | replaces `jobs_profile` |
| **Prose** | Saved long-form writing and how a new question matches it | your voice | replaces the long-form half of the library |
| **Registry** | Deterministic fill rules: questions, patterns, formatters, controls, strategies | *how* to fill | partial |
| **Apply** | Runs one application: open, read, fill, verify, submit | browser session, run record | exists |
| **Repair** | Fixes failed fills in a separate browser while you keep applying | candidate rules | new; extends the failure pipeline |
| **Inbox** | One human queue: new questions, repaired rules awaiting validation, unwritten long-form, audit flags | everything waiting on you | partial |
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

0. **Facts** — catalog, your validation pass, `jobs_facts`, derived facts,
   `jobs_profile` retired
1. **Registry instrumentation** — questions, patterns, formatters, controls,
   strategies, attributable fill events with the stage they stopped at
2. **Repair and validate** — the second-browser repair lane and `hunt validate`
3. **Inbox** — unify `failures list`, `answers review`, new questions, and
   validation
4. **Audit** — per-pair correctness verdicts, sampled
5. **Condenser** — the offline loop
6. **Unattended batch** — last

> **R1 — Facts go first, and Repair moves ahead of Audit.** Patterns point at
> facts, so the fact store has to exist before patterns can be rebuilt on it.
> Repair moves up because you are applying now: every failure that the repair
> lane turns into a validated rule pays off on the very next application,
> whereas Audit only pays off once the Condenser exists to act on its verdicts.

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

**The live run never restarts and never stalls on a fixable error.** A failure
is recorded, the field is handed to you, and the run continues. Fixing happens
in parallel, elsewhere (§10). You are applying now; the system learns beside
you, not in front of you.

**No page is left with a required field empty.** The run does not advance past a
page while any required field is unfilled. When every rule and the model pass
have come up empty, it waits for you.

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
reversible. It happens only in the repair browser (§10).

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

> **R4 — Facts carry a `sensitive` flag.** EEO answers, phone, and address are
> withheld from every model call: the model sees that the fact exists and its
> label, never its value. A sensitive fact still fills deterministically.

> **R5 — Authorization and sponsorship are two facts, not one fact with
> negation.** "Are you authorized to work in the US?" → Yes and "Will you
> require sponsorship?" → No come from `work_auth.authorized_us = true` and
> `work_auth.requires_sponsorship = false`. Storing one and negating it makes
> every pattern carry polarity logic, and the two facts are genuinely
> independent for many people.

**Validation before cut-over.** The fact catalog is generated, then validated by
you, then imported. It is never inferred silently.

1. Generate a draft catalog: every `jobs_profile` column plus every distinct
   value the library restates, deduplicated, with conflicts listed side by side
   ("graduation: profile says 2027-12-15; library also says May 2028, Fall
   2027").
2. You correct it.
3. Import writes `jobs_facts`. Only then is `jobs_profile` retired.

> **R6 — Fact values are reviewed in an untracked local file, not in this
> repository.** The catalog holds phone, address, and EEO answers. The keys and
> types belong in this spec (§7.2); the values belong in the database and in a
> review file under `~/.personal-os/` that is never committed. `os hunt facts
> review` generates it and `os hunt facts import` loads it.

### 7.2 Draft fact catalog

Keys and types only. Values are reviewed in the local file (R6). *Derived*
entries are computed in code and never stored.

| Key | Type | Notes |
|---|---|---|
| `identity.legal_name` | text | |
| `identity.preferred_name` | text | |
| `identity.adult` | boolean | currently a builtin rule |
| `contact.email` | text | |
| `contact.phone` | text | sensitive |
| `contact.linkedin_url` / `github_url` / `portfolio_url` | text | |
| `address.line_1` / `line_2` / `city` / `region` / `postal_code` / `country` | text | sensitive; `city`, `region` answer "current location" |
| `education.school_name` / `degree_type` / `field_of_study` | text | |
| `education.graduation_date` | date | base for every graduation rendering |
| `education.gpa` | number | |
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
| `compensation.minimum` | object | `{amount, currency, period}`; annual is derived (× 2080) |
| `compensation.disclosure` | choice | `number` / `deflect` — see R7 |
| `experience.years_professional` | number | see R8 |
| `experience.prior_internship` | *derived* | `coop.completed_rotations > 0` |
| `employment.worked_for` | *derived* | from resume employers in `artifacts/career/main.json`, given the posting's company |
| `referral.status` | choice | default `none` |
| `eeo.gender_identity` / `transgender` / `race_ethnicities` / `hispanic_latino` / `veteran_status` / `disability_status` | text, boolean, list | sensitive |
| `discovery.target_roles` / `target_locations` / `target_career_stages` | list | not application facts; used by discovery |

Dropped: `completed_at` (a form-completion marker, not a fact),
`professional_summary` (prose — moves to `jobs_prose`), `graduation_term` and
`completed_coop_terms` as stored values (now derived), `earliest_start_text`
(replaced by a date plus `notice_period_weeks`), `work_authorization_status`
and `visa_status` free text (replaced by the R5 booleans and
`work_auth.us_citizen`).

> **R7 — Salary needs a disclosure policy fact.** The library answers "salary
> requirements" with "35" and "salary expectations" with "I'm open to
> discussing…". Both cannot be policy. Proposed: `compensation.disclosure =
> number`; a free-text field gets the formatted minimum, and a required numeric
> field always gets the number. Flip it to `deflect` if you prefer to deflect on
> free text.

> **R8 — `experience.years_professional` is flagged for your check.** The
> backend bootstrap seeds it as `3` when empty (`database.py:80`). Whether that
> is right for an internship candidate is your call during validation; if it
> should follow the co-op count, it becomes derived instead.

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

Derived facts live in one module (`jobs/facts/derived.py`), each declaring its
key, the base facts it reads, and whether it needs today's date or the posting.
They are listed in `os hunt facts list` beside base facts, marked *derived*, so
there is one place to see everything the system will say about you.

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
Rows record dispositions you or the Condenser made, so that they stick.

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
profile and the library: question binding → expression patterns (exactly one
match wins; zero or several pass through) → model pass → you.

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
> are you currently located?" is `address.city` + `address.region`. "Which
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

> **R17 — Texas and the South Central states fall in no acceptable region.**
> Austin and Dallas are common internship offices; as written they are reachable
> only through step 4. Confirm that is intended, or add `US-South`.

> **R18 — Washington is in tier 1 (Seattle) but not tier 2.** A Redmond or
> Bellevue office therefore skips straight to tier 3 (US-West). Proposed: add
> `WA` to tier 2 for symmetry with the other tier 1 cities.

> **R19 — "Remote" ranks after tier 3 and before step 4.** It is chosen only
> when no preferred or acceptable office is offered. Change to tier 1 if you
> would rather take remote over any office.

> **R20 — Multi-select office questions select every tier 1 and tier 2 match.**
> "Select all locations you are open to" is not a single pick; selecting tiers 1
> and 2 states real willingness without opening every acceptable region. If
> none match, it falls back to the single-choice steps.

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
              │             ├─► enqueue repair (§10.3)                │
              │             └─► field handed to you ─► you fill it ───┤
              │                                                        │
              ├─► long-form: nearest prose                            │
              │      ├─ above threshold ─► adapt ─────────────────────┤
              │      └─ below ─► you write ─► saved to prose ─────────┤
              │                                                        ▼
              └─► page gate: every required field filled? ── no ─► wait for you
                                    │ yes
                                    ▼
                         next page … ─► Audit (sampled) ─► submit
```

### 10.2 Required fields

The run never advances past a page with a required field empty. For a required
field, the full order is: pattern → model pass → you. The run waits on the
page, highlights the field in the visible browser, and continues once readback
shows a value. What you type is captured as a `manual` fill, which is exactly
the evidence the Inbox and Condenser need.

> **R21 — Optional fields are attempted fully but never block.** An optional
> field gets every rule and the model pass. If nothing fills it, it is left
> blank and the question goes to the Inbox. Pausing for optional questions
> would make every new form a manual session; leaving required ones blank is
> what the page gate exists to prevent.

### 10.3 Repair: fix in a second browser, never restart

Any failure — format or strategy, on any field — is enqueued for repair and the
live run carries on. A repair worker opens the same application in a **separate
browser context**, advances to the failing step, and works on *only that field*:

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
> first.** A new isolated context in the same Chrome over CDP: no shared
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

> **R24 — Account-gated applications are not repaired on the live draft.**
> Workday and similar ATSs save drafts server-side per account; a second context
> editing the same draft would race the live run. For those, repair waits until
> the live application is submitted or abandoned, or uses another open posting
> on the same tenant that shows the same question.

> **R25 — Repairs run in a background process started by `hunt apply`.**
> One worker, one repair at a time, consuming the queue while you apply. It also
> runs standalone (`os hunt repair`) to drain the queue. Its progress shows as a
> single status line in the apply session, so it never interleaves with prompts
> that need you.

### 10.4 Rule status and `hunt validate`

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

> **R27 — Existing builtin rules start `active`.** The 27 rules in `patterns.py`
> and the current strategies are grandfathered; requiring validation for
> everything already working would stall applying for no gain.

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

## 12. Schema — requires explicit approval

AGENTS.md requires confirmation before any schema entity or field is added.
Nothing below is created until you approve it. Fields marked *derived* are not
stored. Every table also carries `user_id` and `created_at`/`updated_at` as the
existing tables do; they are omitted below.

### New: `jobs_facts` (replaces `jobs_profile`)

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `key` | text | dotted name, unique per user |
| `label` | text | plain-language description shown to you and to the model |
| `value` | jsonb | R3 |
| `value_type` | text | `text` / `boolean` / `number` / `date` / `choice` / `list` / `object` |
| `sensitive` | boolean | R4 |

### Retired: `jobs_profile`

Dropped after the validated import (§7.1). FastAPI's profile endpoints move to
facts; `database.py`'s bootstrap defaults are removed rather than re-seeded.

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
| `resolver_id` | text | existing stable id |
| `kind` | text | `expression` (matches any phrasing) / `binding` (one question → one fact) |
| `expression` | text null | case-insensitive regex for `expression` patterns stored as data |
| `fact_key` | text null | the fact a data pattern reads; null for code-defined builtins |
| `origin` | text | `builtin` / `inbox` / `model` / `condensed` |
| `status` | text | §10.4 |
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
| `status` | text | §10.4 |

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
| `stopped_at` | text null | `pattern` / `format` / `strategy`; null = filled |
| `operated_ok` | boolean | readback passed |

### Changed: `jobs_answer_library` → `jobs_prose`

Renamed, and reduced to long-form writing: `id`, `question_id` (the question it
was written for, R11), `text`, `approved`. The 188 `exact` rows migrate to
facts, bindings, or one-offs; the 2 `pattern` rows become facts plus expression
patterns; `answer_type`, `reuse_policy`, `key`, and `normalized_key` are
dropped.

> **R28 — Rename the table.** Its meaning is changing and its rows are being
> migrated anyway; keeping the name `answer_library` keeps the ambiguity that
> started this. The rename is one migration and a handful of queries in
> `jobs_store.py`.

### Changed: `jobs_application_questions`

Gains `question_id`. It becomes the per-application *answer* record: what was
submitted for each question on each application — which is also where one-off
answers live. Its run-event columns (`run_id`, `event_index`, `event_kind`,
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
failure (DOM signals, artifact paths, intended vs observed value).

### Withdrawn

`review_disposition`, `review_notes`, `reviewed_at` on answer history — the
earlier pending proposal. Question dispositions (§7.4) and rule status (§10.4)
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
| Audit verdicts per pair | Which patterns are wrong vs which controls are broken | Condenser build, module 5 | pattern merge or retire |
| Model-pass `no_fact` names | Which facts are missing most often | 20 `no_fact` verdicts | which facts to ask you for first |
| Prose `none_fits` verdicts with nearest candidate | Where the similarity threshold sits | 30 long-form questions | the threshold value, and whether vectors are needed |

Proposed AGENTS.md convention: *instrumentation collected for later evaluation
records its review trigger and the decision it informs, or it is not collected.*

---

## 14. Migration of existing answers

The 198 library rows are sorted once, with you, during fact validation (§7.1):

| Today | Becomes |
|---|---|
| `exact` row restating a profile value (name, email, LinkedIn ×7, phone ×2) | dropped; the fact covers it |
| `exact` row restating a fact the profile lacks (sponsorship ×~25, citizenship) | one fact; the phrasings become test cases for its pattern |
| `exact` row contradicting a fact ("May 2028") | dropped after you confirm the fact |
| `exact` row specific to one company ("Have you worked for Veeam?") | one-off on that application, or derived (`employment.worked_for`) |
| `exact` row that is really prose | prose |
| `contextual` row | prose |
| `pattern` row (on-site availability, prior internship) | fact plus expression pattern |
| junk ("Search" → "python async") | dropped |

> **R30 — Every migrated phrasing becomes a regression test.** The ~25
> sponsorship phrasings, the graduation phrasings, and the rest are the best
> test corpus available: each must resolve to the same fact through some
> pattern after migration, or the migration lost coverage.

---

## 15. Gaps this surfaces

- **No `uncheck`.** `.check()` is the only check operation. Resets and repair
  replays cannot clear a checkbox set by a prior pass.
- **`select[multiple]` is unhandled.**
- **Radio, lone checkbox, and checkbox group are one branch**, so three distinct
  kinds share one strategy and one undifferentiated failure mode.
- **Generalising the captcha guard changes behavior.** Dropping `ats == "lever"`
  makes it fire on every ATS. That is more correct, and it will look like a drop
  in fill rate until metrics distinguish "refused on purpose" from "failed."
- **AGENTS.md's model-call list is already incomplete.** It names three call
  sites, but `failures diagnose` and `draft-fix` call `claude -p` too
  (`_failure_claude`, `commands.py:615`), and Repair leans on that path.

> **R31 — Amend the AGENTS.md rule to separate fill-time calls from maintenance
> calls.** Fill-time stays at exactly three call sites. Maintenance calls —
> diagnosing a captured failure, drafting a patch for you to review — are
> listed separately and never run inside a live fill. This is a documentation
> fix for something that already exists, made in the same change that builds
> Repair.

---

## 16. Scope

**Module 0, Facts:** `jobs_facts`, the catalog generator, `facts review` and
`facts import`, derived facts including co-op rotations, the library migration
(§14), and retiring `jobs_profile`.

**Module 1, Registry instrumentation:** questions, patterns, formatters,
controls, strategies, fill events with `stopped_at`; generic ARIA detection
replacing the Ashby selector; checkbox group cardinality; classification into
the 7 kinds; the raw attribute vector; refactoring the if/elif dispatch into
control-kind → ordered strategies; `uncheck_control`; `select_multiple`; the
generic captcha guard; the `resolve_priority_answer` split (R14); office
selection (§9); the required-field page gate; the model pass's
`fits_fact`/`no_fact`/`unsure` verdicts.

**Module 2, Repair and validate:** the repair queue, the repair worker and its
isolated context, rule status, `os hunt repair`, `os hunt validate`.

**Later:** Inbox unification, Audit, Condenser, prose vectors, unattended batch.

**Not doing:** vision or computer-use fallback for control operation; CAPTCHA
solving; any port off the `claude -p` CLI.

---

## 17. Recommendations index

| # | Decision |
|---|---|
| R1 | Build Facts first; Repair before Audit |
| R2 | Retire `jobs_profile` entirely |
| R3 | Fact values are `jsonb` typed by `value_type` |
| R4 | `sensitive` facts are withheld from model calls |
| R5 | Authorization and sponsorship are separate facts |
| R6 | Fact values reviewed in an untracked local file |
| R7 | Salary disclosure is a policy fact, default `number` |
| R8 | `years_professional` flagged for your check |
| R9 | `no_fact` names the missing fact |
| R10 | One-offs promote on the second sighting |
| R11 | Long-form matches question to question |
| R12 | Day 1 long-form matching uses the existing drafting call |
| R13 | Embeddings run locally, with an AGENTS.md amendment |
| R14 | Split `resolve_priority_answer` into derived facts and formatters |
| R15 | Office policy applies to office choice, never current location |
| R16 | Option parsing and region table in code |
| R17 | Texas / South Central reachable only via step 4 — confirm |
| R18 | Add `WA` to tier 2 |
| R19 | "Remote" ranks after tier 3 |
| R20 | Multi-select office questions select tiers 1 and 2 |
| R21 | Optional fields are attempted fully but never block |
| R22 | Repair runs in a separate headed context, headless later |
| R23 | Two independent submit guards in Repair |
| R24 | Account-gated drafts are not repaired concurrently |
| R25 | One background repair worker started by `hunt apply` |
| R26 | Validation prefers a fresh posting |
| R27 | Existing builtin rules start `active` |
| R28 | Rename `jobs_answer_library` to `jobs_prose` |
| R29 | Event columns dropped one release after fill events land |
| R30 | Every migrated phrasing becomes a regression test |
| R31 | AGENTS.md separates fill-time from maintenance model calls |

## 18. Open questions

Only the things a recommendation cannot settle:

1. Approval of every schema change in §12.
2. The fact values themselves — settled in your validation pass (§7.1).
3. The long-form similarity threshold — evidence collected per §13 before it is
   fixed.
4. Audit sampling rates and the bound that keeps it off the critical path.
