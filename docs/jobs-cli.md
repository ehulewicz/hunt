# Jobs CLI

All commands run from any directory after the editable worker install.

## Deterministic commands

```bash
os doctor
os hunt sync
os hunt              # = os hunt list
os hunt 20           # = os hunt list 20
os hunt today
os hunt --ats greenhouse         # one form type: greenhouse | ashby | lever | workday | generic
os hunt 50 --ats workday
os hunt today --ats greenhouse
os hunt list 500  # overshooting returns every available match
os hunt list --family software_engineering --location "Atlanta, GA"
os hunt list --all-roles
os hunt open 12
os hunt open 12 --source
os hunt apps
os hunt inbox        # accept or reject what was learned; bind questions to facts
os hunt patterns     # what fills deterministically (active) and what waits (candidate)
os hunt patterns --all
os hunt replay       # the code as it stands, against the answers you accepted
os hunt prose        # saved long-form writing
os hunt prose add --question "Why this company?" --file ./answer.md
os hunt prose approve 2
os hunt archive      # the old answer library, for the facts session only
os hunt archive rules
os hunt archive prose --markdown ~/archived-answers.md
os hunt facts        # = os hunt facts list; every stored and derived fact
os hunt facts list --show
os hunt facts review # accept, edit, or delete each unverified fact
os hunt facts set education.gpa 3.78
os hunt facts set identity.middle_name Alexander   # the catalog gives a known key its type and label
os hunt facts set address.line_2 --none            # the answer is none: free text gets N/A
os hunt facts set work_auth.security_clearance no --type boolean --label "Holds a security clearance"
os hunt facts delete education.gpa       # confirm deleting a stored override
os hunt facts delete address.line_2 --yes # skip confirmation

os hunt resume build      # republish main.json and the PDF from the career record
os hunt resume       # = os hunt resume edit --open
os hunt resume import     # once: load main.json into an empty career record

os hunt queue
os hunt queue add 3 7
os hunt queue empty
os hunt apps
os hunt apps 1 oa
os hunt skipped               # listings passed over with Ctrl+D
os hunt skipped restore 2     # back on the board

os hunt apply 1 --dry-run
os hunt apply 1
os hunt apply                     # top board row, on any site
os hunt apply --ats greenhouse    # top Greenhouse row
os hunt apply today               # first of `os hunt today`, on any site
os hunt apply today --ats greenhouse
os hunt apply 1 --no-claude
os hunt apply 1 --ask-facts  # ask for a fact a question needs, then fill it in
os hunt apply 1 --explain    # print every control's whole decision as it fills
os hunt apply 1 --step       # the same, pausing before each control is filled
os hunt apply 1 --dry-run --explain   # every decision, nothing touched
os hunt apply 1 --done       # applied outside the worker; just record it
os hunt apply https://www.amazon.jobs/en/jobs/123/... --referred "Jane Doe"
                             # any posting URL, a referral link included; NAME optional
os hunt apply <url> --company Amazon --title "SDE Intern"   # name a page that hides it

os hunt apply 1 --fresh       # a new tab; without it, apply continues on the open one
os hunt apply 1 --no-repair   # leave a failed field alone instead of retrying it in place
os hunt repair                # retry queued failures from runs with --no-repair
os hunt validate              # watch each repaired rule fill, then accept or reject it
os hunt apply failures list
os hunt apply failures show 1

os hunt changes               # the 20 newest changes to what Jobs knows, and to its code
os hunt changes 50 --subject code --status passed
os hunt changes show 3        # one change in full, a code change's diff included
os hunt fixes                 # failures the fixer took on, and how each went
os hunt sites                 # each site's latest run and its model calls
```

`facts delete KEY` shows the stored value and asks for confirmation; `--yes`
skips the prompt. It records the deletion through the API. Removing a stored
override lets the career record decide that fact again. A career-derived key
without a stored row must be changed with `os hunt resume edit`; other
computed-only facts belong to code and cannot be deleted. Unknown keys are
reported without sending a delete request.

```bash
os device install    # once per machine: identity, tailscale serve, logon task, doctor
os device status     # this device: address, commit, agent, and its run
os device serve      # the agent itself; the logon task runs it
```

Displayed listing titles are clickable when the terminal supports hyperlinks.
A listing's `#` is assigned by the database over one canonical board. Every
eligible open listing is numbered, whatever its role family, technical families
first and newest first, so a row has the same
number in `os hunt`, `os hunt 9`, `os hunt today`, and every filtered view
(filters leave gaps rather than renumbering). Listings you have applied to are
not shown; a queued one carries a `q`. Nothing is cached on
the machine: `open`, `queue add`, and `apply` ask the API to resolve a number
or an id prefix at the moment you run them, and `apps`, `prose`, and
`apply failures …` count rows in the list they just fetched.
`os hunt open <row>` opens the direct company/ATS URL without creating an
application; `--source` opens the discovery URL.
`queue add` puts listings in the queue; `queue empty` runs `apply` through it in
order, one interactive session at a time, and stops after any that is not
submitted. `apps` lists everything past the queue with its lifecycle status
(`applied`, `oa`, `oa_completed`, `interview`, `offer`, `rejected`); `apps
<row> <status>` moves one along.

## What the system learns

Since 2026-10-14 the system learns answers from scratch (design §0). The old
answer library, profile, and rules are the **archive**: frozen, readable with
`os hunt archive`, and used only to get the facts right in the facts session,
after which it is dropped. No application reads it, not even as a suggestion.
The model never fills a field. When it names the fact that answers a
question, the worker checks what it proposes against every question already
met, then replays it against the answers you accepted on applications that went
in. If it would change none of them for the worse, it is saved as an active
pattern (origin `model`) and fills the field through the ordinary pattern pass,
tagged `learned`, then `pattern`. An expression that would falls back to
binding the question alone; a lesson that still would is left for you, tagged
`left`, and waits in the Inbox as a pattern check. Every change to a pattern or
fact, yours included, is kept with what it was before and after.

Every site is filled the same way, from its controls: there are no per-site
adapters, and a pattern or strategy learned on one board applies to the same
question or control on any other. `--ats` and a run's ATS are only labels.

`os hunt inbox` is the human step. It walks candidates (accept or reject; a
pattern check also says how many accepted answers its replay would change),
questions you answered that nothing is bound to (bind to a fact — `?` lists
every fact key — creating it from the catalog if needed; keep as a one-off; or
mark long-form), and long-form writing from
submitted applications (approve for reuse, or delete). Binding a question then
offers to widen it: a regular expression — proposed from the question's own
distinctive word, or typed — that answers every question matching it from the
same fact, including phrasings no application has used yet. An accepted pattern
is `active`, and the next application fills that question deterministically;
`os hunt patterns` shows the state of the loop. Prose is reused only once
approved; `prose add` saves writing directly, approved.

The career record's exports and the transcript are portable Git artifacts:

```text
artifacts/career/main.json
artifacts/career/Alex_Example.pdf
artifacts/career/Transcript.pdf
```

## Headed apply

Run `os hunt list`, then pass a displayed row or ID prefix:

```bash
os hunt apply 12
```

The command continues on the tab already open for this application when there
is one — so a stopped run, or a re-run after a code change, picks the form up
where it stands instead of navigating away from everything typed into it;
`--fresh` opens a new tab. It waits for the posting to render before reading it
(a board that fills its page from script has no text at all for the first
second or two), checks the page is the posting — it names the role, not only
the company — and otherwise finds it on the site (a link naming the role, the
site's search by the full title and then shorter parts, its open roles), saves
the run/application context,
attaches `artifacts/career/Alex_Example.pdf` (published from the career record), follows one unambiguous Apply
control when the ATS starts on a job-description page, fills the current step,
then watches while you review required fields and use Next or Submit in the
browser. It reports a visible Submit control and rescans after navigation, but
does not gate navigation on a required-field audit. No automated action contains
Next, Submit, signature, or captcha operations; consent and attestation
controls take their agreeing option, through the `consent.agrees` fact and the
pattern that names it, and "how did you hear" takes your standing source through
`preferences.application_source`. Each step ends with a **Left for you**
list of every control still empty — required first, then optional — each with
why (no fact yet, a fact that fits no option, a fact the model said is
missing), and the same controls are outlined on the page. A cover-letter control opens the
letter (from `artifacts/career/cover_letter.md`, slots drafted by Claude) in VS
Code; close the tab to fill or upload it. In the
terminal, Ctrl+S records what has been picked up so
far for review, Ctrl+A records answers and marks the application applied when you have submitted
it yourself, Ctrl+D skips the listing (it leaves the board as `skipped`, a
resync cannot bring it back, and `os hunt skipped restore <n>` can), and
Ctrl+C stops and records the application state. A field fills from a fact,
through an active pattern (tagged `pattern`). A short or choice question no
pattern decides goes to one bounded local Claude pass per page step, which only
names the fact that answers it and may propose an expression for every wording;
the worker saves it as a pattern (tagged `learned`) and the pattern pass fills
the field. A question a pattern decides never goes to the model, even when its
fact is missing. `--no-claude` skips that pass. A textarea is seeded with approved prose
for the same question, else a Claude draft that leans on your earlier writing.

Once every tier has had its turn on a page step, the **audit** looks at what is
still wrong and fixes what it can before telling you anything. A field with no
answer is classified — no fact yet, no pattern yet, the fact fits no option, the
widget refused the value — and each cause runs its own repair: the fact's other
renderings, the control's other strategies, or the question put back to the
model with the options in front of it, which again only teaches a pattern.
Before any model call the audit runs the pattern pass once more, so a value a
pattern filled that the page lost comes back from the pattern, and one that
still will not stay is a control error, never a question for the model. The
audit also reads back the answers already on the page and asks whether a fact
says otherwise; a wrong answer the worker filled has its question bound to that
fact and refilled from it (only when a pattern a model taught gave it, never
one you made), and a wrong answer
*you* typed is reported and left alone. It looks, repairs, and looks again, at most twice, then
lists only what no lane could fix, each line naming its cause. Repair is
budgeted at 25 operations a question and 150 a run, so one bad control cannot
spend a session. `--ask-facts` asks at the terminal for a fact a question needs
and fills the field in once you give it; without it the gap is reported.

`--explain` prints, for every control, the five parts of how it was decided:
the **control** (its kind, the attributes that identify it on any site, its
flags, its options), the **question** and the key patterns match on, the
**planner**'s reason (an engine rule, the resolver, the non-answer guard, or a
choice group answered by another option), the **resolver**'s steps (the
binding for this question, the expressions that match, which decided, the fact
and its value, the formatter that wrote it, every other rendering), and the
**operation** (strategies in order, each attempt and its error, the readback).
Controls nothing was planned for are listed after each pass with why.
`--step` does the same and pauses before each fill with the control outlined
on the page: Enter fills it, `s` leaves it for you, `c` stops pausing.

A board that puts a choice between Apply and the form — Workday's "Autofill
with Resume / Apply Manually / Use My Last Application" — is a **gate**, and it
is answered like any other choice: by the `preferences.application_start` fact,
through an active pattern first, else one bounded Claude pass naming which
fact answers it, which binds the gate's question to that fact. Nothing is clicked without a fact behind it, the page's own
submit and your account's sign-in are never candidates, and the readback is the
form appearing. A gate no fact answers is left for you with its choices named —
and the one you click is recorded, so the Inbox can bind it and the next run
passes the gate on its own.

An **account wall** — a sign-in, create-account or verify-your-email page with
no application form on it — is the one place the worker operates your account.
It signs in as the `contact.email` fact: through "Sign in / Continue with
Google" when the site offers it, else with the reusable ATS password; when the
site has no account it creates one and finishes verification from the mailed
link or code, and when the account exists under another password it resets it
through the mailed reset link. While the page is an account page it may click
sign-in, create-account, Google, forgot-password and the account form's own
button; it never clicks the application's Next or Submit, and a captcha parks
it. Each step runs once, so a site that keeps refusing is reported once as an
`account_wall` failure with what it said, and the wall is yours from then on —
the run watches the page while you finish. A form that only offers "Apply with
Google" or a password to save your progress stays a form. `--dry-run` never
signs in. The one-time setup is in `../workers/README.md`.

Ashby's Overview tab is a recognized standby state. Return to Application and
the same step continues without refilling or another Claude pass. A submitted
success panel ends automatically. On detected submission, Ctrl+A, Ctrl+D, or
Ctrl+C, persistence finishes before the command closes only the application tab
it opened. `--dry-run` and unexpected worker errors leave that tab open.

Values are rendered from facts at fill time: pay requirements follow the
`compensation.minimum` fact, USD $40/hour (or 40 numeric), and explicit annual
requirements are $83,200 USD (or 83200 numeric). Graduation uses `Fall 2027`
when a term is requested, `December 2027` otherwise, and whichever rendering a
dropdown offers. An office choice bound to `preferences.location_strategy`
follows the §9 tiers. Completed co-op rotations are derived, never stored: the
`coop.anchor` fact says four were complete on 2026-08-01, and one more completes
each May 1, August 1, and November 1 until graduation. Visa/work-permit
expiration stays manual without a date.

Facts are the single source the apply run fills from. `os hunt facts review`
shows each unverified fact beside what the archived answers said to similar
questions, so a contradiction such as a second graduation date is settled once,
on the fact.

## From the Hunt page

The Vercel Hunt page is the primary Hunt control. Every device that ran `os device
install` lists itself there while its agent is up; pick one, then apply to a board
number, an id prefix, or a posting URL, or press "Next on board". The agent brings
the checkout current first (a clean checkout of `main` fast-forwards; one with
local changes runs as it is, and the page says so), then runs `os hunt apply <ref>
--remote`. Its Chrome tab is beside the run, live and clickable: you sign in, solve
a captcha, and press Submit there, then press Submitted. Save, Submitted, Skip, and
Stop on the page are Ctrl+S, Ctrl+A, Ctrl+D, and Ctrl+C. The cover letter opens on
the page instead of VS Code, and a "Left for you" fact gap takes its value on the
page: the run stores the fact and fills the field, exactly as `--ask-facts` would.

`os hunt apply` in a terminal works as before. When the device's agent is running,
the run registers with it, so it appears on the page too and the page's controls
work; its letter stays in VS Code. A device runs one application at a time: the
page cannot start a run while one is active, and a second terminal run while the
page shows one just runs unmirrored.

## Repair

A field that fails stops at `format` (the fact fit nothing the control offered)
or `strategy` (the widget would not take the value), and the failure says which.
Apply retries each such field in place, on the page you are filling, right
after the step's fill, on any site (`--no-repair` turns this off): every strategy that applies to the
control for a strategy failure, every other rendering of the fact for a format
failure, each checked by readback. What worked fills the field now, is stored on
the issue as a candidate, and the issue becomes `repaired`; the terminal tags
the line `repaired`. The retry never operates a control that reads as
submitting. `os hunt repair` does the same for failures left by
`--no-repair` runs, in a new, isolated browser context, where a page script also
cancels every form submission and submit-labelled click and a network guard
aborts any non-GET request that names a submission.

`os hunt validate` walks the repaired issues. It opens a visible page on an
unsubmitted application that showed the question, another posting first, fills
only that field with the candidate, outlines it, and asks. Accepting a strategy
makes it that control's first strategy in every later run; accepting a format
binds the question to the fact with that formatter. Rejecting sends the issue
back to the queue. Pattern-stage failures never come here: no retry invents a
fact, so they go to the Inbox. On account-gated boards (Workday) the isolated
retry cannot reach the form; apply's own retry can, because it works on your
signed-in page.

`os hunt apply failures list` and `show` read what runs could not settle, with
its evidence. Fixing is the run's own job, during the run: a widget nothing can
operate, a strategy or rendering the retries ran out of, a wall before the form,
or a posting the site's search could not find goes to the fixer while the run
goes on. It writes a code fix and a test in a scratch clone, tests and replays
it, tries it on your page with the submit guards on, and commits and pushes it
once the value reads back; the run then reloads on the same tab and carries on.
The terminal and the page show each fix as it goes (`fix writing`, `testing`,
`waiting`, `probing`, `kept`, `unsolved`). What three attempts could not fix is
outlined and waits in the Inbox.

## History

Three read-only commands say what the loop has done. `os hunt changes [N]` lists
the newest N changes (20 by default), newest first: when, the subject and its id
(a fact by key, code by commit), the action, who made it (`you`, `inbox`,
`model`, `repair`, `worker`), the failure it fixed (its run, issue, and
question), its replay (`passed 0/12` is none wrong of twelve replayed), and what
it did in one line, the fields that differ before to after. `--subject`
(`pattern`, `fact`, `code`, `prose`, `question`, `strategy`) and `--status`
(`pending`, `passed`, `failed`) narrow it. `os hunt changes show <row|id-prefix>`
prints one change in full, with both sides as JSON and a code change's diff.

`os hunt fixes` lists the failures the fixer took on (`fixing`, `fixed`,
`unsolved`): the category, the question, how often it was seen, the commit that
fixed it, and the notes, which say what it tried or why it gave up.

`os hunt sites` groups runs by site, the registrable host (its last two labels)
of where each run ended up, else where it started, and shows each site's latest
run: when, its model calls, and how many failure issues it hit are still open or
unsolved. A site whose latest run made no model calls is marked `met`: the
next application there should need none either.
