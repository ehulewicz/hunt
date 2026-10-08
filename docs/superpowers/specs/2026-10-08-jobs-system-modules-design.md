# Jobs system: modules, registry, and the application path

Date: 2026-10-08. Status: proposed, awaiting schema sign-off.

This document describes the Jobs system as a set of modules with explicit
boundaries, and specifies the first of them in enough detail to build. It
replaces the implicit structure that grew inside `workers/` with named parts,
measurable rules, and a path that gets more deterministic over time.

Nothing here is built. Every schema addition in §10 needs explicit confirmation
before implementation, per AGENTS.md.

---

## 1. Why

Applying works today but cannot improve itself. Three specific defects:

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

The goal is a system where every fill is attributable to exactly one pattern and
one control, every outcome is a measurement, and accumulated measurements move
fills down a ladder from manual, to model-assisted, to fully deterministic.

---

## 2. Current state

### Exists and is kept

- `AnswerResolution(value, source, resolver_id)` — `autofill.py:14`. Stable
  resolver ids already in use: `builtin:profile_field_v1`, `library:{id}`,
  pattern rule names.
- Per-field detection returning `tag, type, name, label, group, required,
  selector, role, max_length, placeholder, autocomplete, section, options[],
  value, filled` — `browser.py:840-888`.
- `verify_control` readback, including the upload and combobox mirror paths —
  `browser.py:575`.
- Eight operation methods, six of them already named functions:
  `set_input_files`, `option_match` + `select_option(label=)`,
  `select_option(value=)`, `choose_combobox_option`, `select_combobox_option`,
  button click + `aria-pressed` verify, `control.check()`,
  `set_registered_value`.
- Failure capture and the `list | show | diagnose | draft-fix | apply-fix |
  verify` pipeline — `commands/hunt.py:647`.
- Answer history with provenance in `jobs_application_questions`.

### Missing

- Any record of a successful fill at control grain.
- Stable, ATS-independent control identity.
- Named strategies; they are if/elif branches.
- `uncheck`. `.check()` is the only check operation in the file.
- `select[multiple]` handling.
- Distinction between radio, lone checkbox, and checkbox group — all one branch.

---

## 3. Modules

| Module | Does | Owns | Status |
|---|---|---|---|
| **Apply** | Runs one application: open, read page, fill, long-form, verify, submit | browser session, run record | exists |
| **Registry** | The deterministic fill rules: controls, strategies, patterns, questions | *how* to fill | partial |
| **Answer Bank** | Short-form and long-form answers, approval state, similarity | your voice and facts | exists |
| **Audit** | Judges filled pairs for correctness before submit | the correctness verdict | new |
| **Condenser** | Daily offline loop turning history into new/merged rules | rules from evidence | new |
| **Inbox** | One human queue: unwritten long-form, unassigned questions, failed controls, proposals | everything waiting on you | partial |

### Flow

```
   hunt apply ──► Apply ──► fill_events (question, pattern, control, strategy, outcome)
                   │                                    │
       can't fill  │                                    ▼
       / no answer │                            Condenser (daily)
                   ▼                                    │
                 Inbox ◄───────── proposals ────────────┘
                   │                                    ▲
         you resolve│                                    │
                   ▼                                    │
              Answer Bank ──────────────────────────────┘
                   │
                   └──► Registry ──► (reset) ──► Apply re-runs deterministically

   Apply complete ──► Audit ──► verdicts ──► Inbox + fill_events ──► submit
```

### Build order

1. **Registry instrumentation** — controls, strategies, attributable fill events
2. **Audit** — per-pair correctness verdicts, sampled
3. **Inbox** — unify `failures list` and `answers review`
4. **Condenser** — the daily loop
5. **Unattended batch** — last

The order is a dependency chain, not a preference. The Condenser has nothing to
condense until many applications' worth of attributed fills and audit verdicts
exist. Audit verdicts are not actionable until fills are attributable to a rule.
So: instrument, judge, accumulate, condense.

---

## 4. Core decisions

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
success rate tells you nothing about what succeeded. Strategy is recorded as a
dimension on the event, not as a second chosen component.

**No stored counters.** `fill_events` is the only thing written during a run.
Every rate — per control, per pattern, per pair, per strategy, per ATS — is a
query over it. This removes an entire class of bug: there is no two-phase
counter update to tangle with the atomic run-completion transaction, and no
marginal counter that can drift from the pair counter it should agree with.

**Everything retires, nothing is deleted.** `enabled` on controls, strategies,
and patterns. Reads return enabled rows only. Condensing sets the superseded
rows disabled rather than removing them, which makes a merge reversible — if the
condensed pattern performs worse than the two it replaced, re-enable them.

**The determinism ladder.** Three rungs, for both answers and controls:

1. manual — you do it
2. model-assisted — deterministic narrowing to N candidates, model picks one
3. deterministic — a single rule

Each rung's output is the next rung's evidence. The model's choices at rung 2
are precisely the signal the Condenser uses to collapse N to 1. Allowing
multiple candidates is therefore not a retreat from determinism; it is the
mechanism that reaches it. A rule that stays at rung 2 forever is visible and
measurable rather than hidden over-fitting.

**Probing is a discovery operation, never a live one.** Clicking a widget to
learn how it behaves mutates the form, can fire side effects (revealing
follow-up questions, triggering validation, autosaving), and is not always
reversible. It runs in the Registry's discovery path against an application
already being reset, never during a live apply.

**CAPTCHAs are detected and handed off, never solved.** Detection becomes
generic by dropping the `ats == "lever"` conjunct; the selectors are already
generic. A captcha present means the control is parked and goes to the Inbox.
Circumventing anti-automation measures is out of scope for this system.

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

The eight that exist today, named, plus one that does not:

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

## 7. Patterns and questions

**Question** — a normalized label, via the existing `normalize_question`. It is
what is being asked, independent of widget and of company phrasing. A question
with no pattern bound to it is an Inbox item.

**Pattern** — a rule that produces a value for a question. Identity is the
existing `resolver_id`. Patterns already exist in `patterns.py` as `BuiltinRule`
and are already recorded on fills; this adds the `enabled` flag and the metric
join, not the concept.

**How patterns get built.** Three origins:

1. *Builtin* — hand-written rules in `patterns.py`, as today.
2. *Approved answer* — you answer a question in the Inbox; it becomes a
   `library:{id}` pattern bound to that question.
3. *Condensed* — the Condenser proposes a new pattern or an addition to an
   existing one from accumulated answers and verdicts, and you approve it.

A condensed pattern that generalises two existing ones supersedes them: the new
row is enabled, the two originals are disabled, and all three remain queryable
so the merge can be judged and reversed.

---

## 8. Metrics

One event row per fill attempt:

```
fill_event(question, pattern, control, strategy, ats, value_hash,
           operated_ok, outcome, run, step, created_at)
```

`value_hash` rather than the value itself: the answer text already lives in
`jobs_application_questions`, and duplicating it here would create a second copy
to keep correct. The hash is enough to ask "same value, different outcome?",
which is the only question this table needs it for.

Two distinct notions of success, deliberately separated:

- **Operational success** — the widget accepted the value; `verify_control`
  readback passed. Known immediately, written during the run.
- **Answer correctness** — the value was right. Known later, from an Audit
  verdict or from whether you edited it. Written as a separate verdict row
  referencing the fill event.

Rates are queries:

- control success = operated_ok / attempted, grouped by control
- pattern success = confirmed_correct / used, grouped by pattern
- pair success = both, grouped by (control, pattern) — the fill *rule*
- strategy success = operated_ok, grouped by (control, strategy)
- per-ATS breakdown = any of the above, grouped additionally by ats

The pair is the unit worth watching, because it catches interaction failures the
marginals hide: a pattern 95% correct overall and a control 98% operable overall
can still fail consistently together when the pattern emits a value the widget
cannot represent.

At current scale — 854 listings, low thousands of fill events — an index on
`(control_id, pattern_id)` is sufficient. Materialised rollups are premature.

---

## 9. Application-level behavior

The path a single application takes.

```
hunt apply
   └─► open browser + application
          └─► grab context + next page
                 ├─► deterministic autofill  (pattern × control × strategy)
                 │      ├─ filled + readback ok ──────────┐
                 │      ├─ no pattern ──► Inbox (unassigned question)
                 │      └─ control failed ──► Registry discovery ──► reset
                 │
                 └─► evaluate long-form questions
                        ├─ similarity >= threshold ──► build from saved + fill
                        └─ similarity <  threshold ──► notify + you write it
                                                          └─► save to Answer Bank
                                                                      │
                 ┌────────────────────────────────────────────────────┘
                 ▼
          optional manual verify
                 │
                 ▼
          Audit: verify correctness   (sampled: FAANG-tier, first of day, random)
                 │
                 ├─ clean ──────────────► submit
                 └─ flagged ────────────► Inbox, application parked
```

**Long-form threshold.** When a question is at or above the similarity threshold
to something you have already written, the answer is adapted from yours — ideally
reusing the existing text and changing a sentence, or combining two. Below the
threshold it is not generated; it goes to you to write, which keeps your voice
and builds the bank. The threshold is a tunable, starting around 0.70–0.80.

**Audit sampling.** Not every application. FAANG-tier companies always, plus the
first of each day and a random sample, bounded so rate limits are not the
constraint. Audit sees all of an application's pairs in one call: for
*evaluation* (unlike generation) shared context is an advantage, because it
catches internal inconsistency — 4.5 co-op terms in one answer and 4 in another —
that per-pair checks structurally cannot see.

**On a control failure.** The control is parked, the application continues with
the rest, and the failure goes to Registry discovery. Once a workaround exists,
the application is reset and re-filled deterministically. For late-stage failures
— page 4 of 5, after an upload — finishing the one field by hand is cheaper than
a reset, and still produces the record that feeds discovery. Re-running is for
*learning*; it does not have to be how every individual application completes.

**No vision fallback.** A model driving pixels to rescue a stuck control would
complete that application and destroy the signal that the control is broken, so
the fix would never be made. Diagnosis amortises per control type; a pixel
fallback pays per application forever, at materially lower accuracy. The backup
for "complete this one anyway" is you, at a browser that is already visible.

---

## 10. Schema — requires explicit approval

AGENTS.md requires confirmation before any schema entity or field is added.
Nothing below is created until you approve it. Fields marked *derived* are not
stored.

### `jobs_controls`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `kind` | text | one of the 7 in §5 |
| `attributes` | jsonb | raw observed attribute vector (§11) |
| `enabled` | boolean | default true |
| `first_seen_at` | timestamptz | |
| success rate | *derived* | query over `jobs_fill_events` |

### `jobs_strategies`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `name` | text | e.g. `select_by_label` |
| `control_kind` | text | the kind it operates |
| `precedence` | integer | candidate ordering within a kind |
| `enabled` | boolean | default true |

### `jobs_questions`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `normalized_key` | text | via existing `normalize_question` |
| `sample_label` | text | one observed raw phrasing, for display |
| `pattern_id` | uuid null | null = Inbox item |

### `jobs_patterns`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `resolver_id` | text | existing stable id |
| `origin` | text | `builtin` / `approved_answer` / `condensed` |
| `superseded_by` | uuid null | set when condensed |
| `enabled` | boolean | default true |

### `jobs_fill_events`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `run_id` | uuid | |
| `step` | integer | |
| `question_id` | uuid | |
| `pattern_id` | uuid null | null = no pattern matched |
| `control_id` | uuid | |
| `strategy_id` | uuid null | null = never attempted |
| `value_hash` | text | hash only; the value itself stays in `jobs_application_questions` |
| `ats` | text | dimension only, never control identity |
| `operated_ok` | boolean | readback passed |
| `outcome` | text | `filled` / `parked_no_pattern` / `control_failed` / `captcha_guard` |
| `created_at` | timestamptz | |

Answer *correctness* is not on this row. It arrives later as an Audit verdict
referencing `fill_event_id`, because it is known at a different time and by a
different process.

### Also pending, from earlier work

`review_disposition`, `review_notes`, `reviewed_at` on answer history, noted in
CURRENT.md as awaiting confirmation. Worth resolving in the same pass rather
than carrying two open schema questions.

---

## 11. Deferred evidence

Instrumentation collected now for later evaluation. Each entry records a review
trigger and the decision it informs; without both, the field is not collected.
This is a gate as much as a record — speculative collection is not free, and a
column nothing reads is either future evidence or future cruft, with nothing but
this table distinguishing them.

| Collected | Question it answers later | Review trigger | Decision it informs |
|---|---|---|---|
| Raw control attribute vector | Is a kind really two kinds? | a kind's strategy success goes bimodal, or n >= 50 observations | split `dynamic_choice` into local-filter vs server-search |
| Typeahead behavior — does a menu appear on keystroke | Which combobox subkinds need which strategy | first repeated failure of a `dynamic_choice` strategy | add a strategy, or split the kind |
| `aria-*` state attributes beyond those dispatched on | Which generic signals predict operability | before adding any new ATS-specific selector | replace a hardcoded selector with a generic one |
| Audit verdicts per pair | Which patterns are wrong vs which controls are broken | Condenser build, module 4 | pattern merge or retire |

Proposed AGENTS.md convention: *instrumentation collected for later evaluation
records its review trigger and the decision it informs, or it is not collected.*

---

## 12. Gaps this surfaces

Found while designing, not previously tracked:

- **No `uncheck`.** `.check()` is the only check operation. The reset-and-refill
  loop cannot clear a checkbox set by a prior pass. Latent today, live the moment
  resets begin.
- **`select[multiple]` is unhandled.**
- **Radio, lone checkbox, and checkbox group are one branch**, so three distinct
  kinds share one strategy and one undifferentiated failure mode.
- **Generalising the captcha guard changes behavior.** Dropping `ats == "lever"`
  makes it fire on every ATS. That is more correct, and it will look like a drop
  in fill rate until metrics distinguish "refused on purpose" from "failed."

---

## 13. Scope

**In, for Registry instrumentation (module 1):** the five tables above, generic
ARIA detection replacing the Ashby selector, checkbox group cardinality,
classification into the 7 kinds, the raw attribute vector, refactoring the
if/elif dispatch into control-kind → ordered strategies, `uncheck_control`,
`select_multiple`, and the generic captcha guard.

**Out, for later modules:** Audit, Inbox unification, Condenser, unattended
batch, the long-form similarity threshold tuning.

**Not doing:** vision or computer-use fallback for control operation; CAPTCHA
solving; any port off the `claude -p` CLI.

---

## 14. Open questions

1. Approval of every field in §10, and whether the pending
   `review_disposition` fields land in the same pass.
2. Whether `uncheck_control` belongs in module 1. Recommended yes — the dispatch
   is being refactored anyway, so it is nearly free now and a second pass later.
3. The long-form similarity threshold's starting value.
4. Audit sampling rates, and the bound that keeps it off the critical path.
