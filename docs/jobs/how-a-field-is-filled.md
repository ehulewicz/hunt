# How a field is filled

The pieces Jobs uses to fill one form field, and where each one lives. This is
the map across boundaries. How each piece works is documented once, in the
README linked under [Where the details are](#where-the-details-are).

```
WHAT TO ENTER                                   HOW TO ENTER IT

label on the page                               the same field's widget
  │                                               │
  ▼                                               ▼
QUESTION    what's asked                        CONTROL     which widget
  │                                               │
  ▼                                               │
PATTERN     which fact answers it                 │
  │                                               │
  ▼                                               │
FACT        what's true                           │
  │                                               │
  ▼                                               │
FORMATTER   how to write it for this control ◄────┤
  │                                               │
  │                                               ▼
  └──────────── value ────────────────────────► STRATEGY    how to operate it
                                                  │
                                                  ▼
                                                the page, then read back
                                                  │
                                                  ▼
                                                FILL EVENT + ANSWER HISTORY
```

| Piece | What it is | Where it lives | How it counts as verified |
|---|---|---|---|
| **Question** | A label on a control. Each wording gets its own row | `jobs_questions` | Disposition: unbound, pattern, one-off or long-form |
| **Fact** | A typed value about you, like email or sponsorship = no. Some come from the career record, and a few only exist during a run (referral, applied before) | `jobs_facts` | `verified_at` is set |
| **Pattern** | The link from a question to a fact. A binding covers one exact question; an expression is a regex that covers any wording | `jobs_patterns` | Status goes candidate → active → retired or rejected |
| **Formatter** | Code that writes a fact for one control: no → "No", the phone number in the right shape, or the matching dropdown option | worker code (`jobs/formatters.py`) | n/a |
| **Control** | The widget, identified by one of 7 kinds (text, select, radio group, multi-choice, toggle, typeahead, file) plus its structure. Never by question or ATS | `jobs_controls`, for tracking only | n/a |
| **Strategy** | The Python operation that puts a value into that kind of widget: type, pick an option, click a radio, type then pick a suggestion | worker code (`jobs/registry/strategies.py`, `browser.OPERATIONS`). `jobs_strategies` only tracks names | A validated repair can prefer a strategy for one control |
| **Fill event** | One row per attempt: question, control, strategy, fact, formatter, and where it stopped | `jobs_fill_events` | n/a |
| **Answer history** | What the page actually held, and who put it there: pattern, model, you, or reused prose | `jobs_application_questions` | Marked submitted or not |
| **Prose** | Long-form writing, on its own track. Reused for the same question once approved | `jobs_prose` | `approved` is set |
| **Archive** | The answer library from before the Oct 14 restart | `jobs_archive_answers` | Read-only, and no run reads it |

## Where the details are

| For | Read |
|---|---|
| Resolution order, formatters, the audit, controls and strategies | [`workers/README.md` — How a field is filled](../../workers/README.md#how-a-field-is-filled) |
| Repair and validated strategy preferences | [`workers/README.md` — Repair](../../workers/README.md#repair) |
| Questions, patterns, prose, the archive, and the Inbox endpoints | [`backend/README.md` — Questions, patterns, prose, and the archive](../../backend/README.md#questions-patterns-prose-and-the-archive) |
| The tables themselves | [`database/README.md`](../../database/README.md) |
| The commands that show each piece | [`jobs-cli.md`](../jobs-cli.md) |
