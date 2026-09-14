# Resume defense

Interview-facing companion to `main.json`. Every bullet on the page is one
rendered line, so each one compresses far more than it states. This file records
what was compressed, what the honest expanded answer is, and what must not be
claimed without checking sources first.

The writing pass is complete; its decisions are reflected in `main.json` and AGENTS.md.

Last updated: 2026-09-11.

## Education, Example University, BS CS, May 2028, 3.78

- The heading and coursework are populated. No education bullet is planned.

## Experience

### Middleware Engineer | Northwind, May 2025 – Present

Current bullets (settled 2026-09-11):

1. Engineered a notes and reporting system, proposing linking across ERP
   modules: customer signed second SOW on delivery
2. Built a chunked full-stack bulk action system updating 1,000-task batches
   and their subtasks with per-family rollback
3. Owned a 41-ticket modernization of PHP pages into AJAX-driven API on
   PostgreSQL and a rebuilt JS asset library
4. Led both customer SOWs end to end and shipped 120+ production tickets across
   the wider product alongside two other teams

Each line has one job: judgment that made money, one hard system, legacy work
at scale, breadth of the role. Verbs are deliberately plain (engineered, built,
owned, led). "Architected" and "delivered" were rejected as showy or evasive.

**Product.** Northwind calls its product xERP. Say "ERP" on the page, "xERP"
in conversation. The bullet-2 nouns are the product's task model: tasks contain
subtasks, and a task with its subtasks is a family.

**Bullet 1, the notes system.** Internal name is Program Notes. The customer
asked for notes. The cross-module linking across Projects, Resets, and
Locations was the user's proposal, accepted and built, and that quality is what
made the customer come back. The second SOW was a different project, not a
continuation, and the customer initiated it on delivery of the first. "Signed"
is the verb because an SOW is a document and it was signed. The colon is a
deliberate pause before the payoff, chosen over an em dash because the em dash
now reads as machine-written.

**Bullet 2, the bulk action system.** Full design detail, all of it interview
material:

- Chunked 10-group runner shared across update and cancel flows, selections
  capped at 1,000 items, so large batches neither exhaust client memory nor time
  out a request.
- Per-family atomicity on bulk cancel via savepoints. If a child task fails to
  cancel, its whole family tree rolls back without failing unrelated families in
  the same batch. This is the "per-family rollback" on the page.
- Fail-fast on connection drop: execution halts rather than cascading false
  failures across later chunks.
- A dedicated internal hook for recursive subtask cancellation instead of
  reusing the public child-status hook, which discarded child return statuses
  and exposed an unsafe public flag. `internal_allowed` guards stay in place so
  external endpoints cannot trigger recursion.
- Reset resolution via `top_level_graph_type_table_name` and
  `top_level_graph_pk_val` rather than `reset_task_reset`, avoiding an implicit
  INNER JOIN that silently excluded non-reset tasks.
- One unified confirmation modal warning not to navigate away during chunk
  execution, replacing several prompts.
- Errors grouped by reason rather than task id, so a hundred items failing the
  same validation rule do not truncate the browser alert.

Multi-tenancy was dropped from the line because any SaaS ERP is multi-tenant.
It is the floor, not a feature.

**Bullet 3, the modernization.** Accurate architecture: server-rendered PHP
pages split into a static PHP shell, new PHP API routes on PostgreSQL, and an
overhauled JS asset library doing the dynamic work over AJAX. PHP was kept, not
replaced. 41 tickets after a recount (an earlier 54 was wrong). "Zero downtime"
was removed because the platform's deployment handles that, not this work.
"Full behavior parity" is true and is the honest answer to "what made it hard,"
but it lost the character budget to the JS library. The API-route side was
still in progress as of 2026-09-11, so answer "is it done?" with: the JS
overhaul shipped, the API routes are mid-migration.

**Bullet 4, breadth.** Settled facts:

- **2** customer Statements of Work, both led, both carried nearly end to end.
  The earlier "4 Statements of Work" claim was wrong and came from ChatGPT
  output. Never restore it.
- Roughly 93% of the code written solo. **Deliberately kept off the page.** Bad
  provenance (93% of what, exactly), and a number that precise is implicitly a
  claim about what teammates did not do. "Led" and "owned" are scope claims
  rather than measurements and cannot be litigated.
- "Alongside two other teams" was chosen over naming frontend and database
  teams. The seam is still open: what those teams actually delivered on the SOWs
  has never been recorded. This was left unrecorded when the writing pass closed.

Anticipated probes:

- *"You led two SOWs, but also wrote nearly all the code. Which was it?"* The
  weak seam. Have a concrete answer about what the other teams owned.
- *"What is a task family?"* The bullet-2 question you want. Answer with the
  per-family savepoint design.
- *"Is the modernization finished?"* See bullet 3 above.

### Technical Co-founder | Northhealth AI, October 2025 – May 2026

Three blank slots for zero-to-one product work and founder ownership.

Recoverable material from the pre-rewrite version at `38edffe`:

- Clinician-first rehab platform turning continuous patient data into provider
  insight at visits and personalized recovery support between visits.
- Led engineering and shipped to a v1 release, the version used to begin
  clinical trials.
- Accepted to present at the American Academy of Neurology (AAN) in 2026.
- Validated direction with 22 healthcare organizations and professionals, and
  fed clinician feedback into MVP scope, safety constraints, and workflow design.

### Software Engineer (Contract) | Example Public Schools, May 2023 – December 2023

Current bullets:

1. Automated deterministic leave cases across 9 HR and payroll systems, cutting
   case handling from 10 minutes to 3.
2. Designed a unified data model and adapter layer over inconsistent schemas,
   automating browsers where systems had no API.
3. Owned the tool after launch, rebuilding integrations as systems changed and
   eliminating a 2 to 3 minute daily login.

**Employment relationship.** Not a direct CPS employee. Worked under a SWE
contractor (the user's father) on a CPS engagement, and built a tool used by a
CPS team. "Software Engineer (Contract) | Example Public Schools" therefore
implies a direct CPS relationship that did not exist. "Subcontractor" is more
defensible. Most precise would be the contracting entity's name with CPS named
in a bullet, but that entity name is not recorded. Unresolved.

**Who actually used it.** Not engineers. The users were the district's **Leave
Specialists**, an HR, payroll, and timekeeping function. This was the most
significant correction to the entry: the resume signal is shipping an internal
tool to a non-technical operations team in a rule-heavy domain, which is rare
for a student.

**The domain.** Employee leave administration. Resolving a case meant
determining pay eligibility and amount, approval, hour logging, part-time versus
full-time employment status, and which of several leave types applied. Done
manually it required moving between multiple enterprise systems with inconsistent
schemas, missing or legacy data, and system-specific edge cases.

**The deterministic versus judgment split.** A large share of cases were
rule-bound, and those the tool resolved outright with no human in the loop. What
remained were the genuinely hard cases that required a person, and for those the
tool consolidated everything the specialist needed into one real-time interface
so the call was faster and better informed. Both halves are true and the order
matters: it automated the majority and supported the remainder.

**Architecture.** Source-specific adapters normalize each system into one common
domain model. That model drives deterministic updates across downstream systems.
Browser automation is isolated to the systems that had no usable API, so the
Selenium surface does not leak into the rest of the design.

**Scale.** 9 heterogeneous systems: databases, internal tools, and web
interfaces. Oracle and SQL data access are confirmed. Salesforce was mentioned in
older notes as possibly one of them.

**Technologies with solid support.** Java, SQL, Oracle, Selenium.

**The 70% metric.** 10 minutes down to 3 on the core task. Unlike the Northwind
93%, this has defensible provenance: a single named task, measured start to
finish. Safe to state and safe to be asked about.

**Timeline and maintenance.** Development completed in August 2023. Maintenance
ran August through December 2023, roughly four months. The majority of that work
was overhauling the tool as the upstream systems it integrated changed, which is
the standard way a nine-system integration dies and this one did not. One named
feature request landed in that window: automating login across the connected
tools. The first login of the day cost 2 to 3 minutes on its own, and moving it
to the background was a real efficiency and quality-of-life gain for the
specialists.

What the one-line bullets had to shed, and is therefore yours to supply in
conversation:

- Bullet 1 dropped "timekeeping" from the system list, the human-in-the-loop
  half, and the unquantified error reduction. The page now reads as more fully
  automated than the truth, so expect and welcome the probe.
- Bullet 2 dropped "legacy edge cases" and "deterministic cross-system
  synchronization." The browser-automation clause was kept deliberately as the
  most concrete evidence that these were genuinely messy legacy systems.
- Bullet 3 dropped "upstream" before "systems changed," and dropped that the
  login work was a stakeholder request rather than self-directed.

Anticipated probes:

- *"So it decided eligibility?"* Yes for the rule-bound cases, and for the
  ambiguous ones it pulled everything the specialist needed into one place. This
  is a strong answer, not a walk-back. The bullet is written to invite it.
- *"70% of what?"* One named task, 10 minutes to 3.
- *"Which nine systems?"* Only partially recoverable. See below.

Do not claim without checking the code or records: the exact list of all 9
systems, the number of specialists who used the tool, any ticket count, the
contracting entity name, the employment classification, and the specific
implementation of retries or state checks. The error-reduction claim has no
measurement behind it and stays unquantified.

## Research

### Undergraduate Researcher | Sample Lab @ Example University Europe

Three blank slots for machine-learning research, experimental honesty, and
technical communication.

### Undergraduate Researcher | Example Lab @ Example University

Three blank slots for simulation and systems engineering.

## Projects

### AlphaResearch

Current bullets:

1. Built a platform where AI agents broke research questions into hypotheses and
   ran experiments in parallel.
2. Designed the cloud system for 1,000+ AI agents, giving each an isolated
   workspace under one shared compute budget.
3. Reached 500,000+ simulation steps per second by running experiments
   concurrently across cloud environments.

The heading already carries the UC Berkeley AI Hackathon win, so the bullets do
not repeat it. The wording deliberately separates product purpose, personally
owned infrastructure, and measured throughput. Named technologies remain in
Technical Skills rather than turning the project into a stack inventory.

### Personal Dynamics Engine

Three blank slots. The least defined entry on the page, with no written content
anywhere.

## Technical Skills

Skills use three rows matching the Sam Kim reference: Languages, Frameworks &
Tools, and Libraries. This section is the only place technologies are listed.

- FastAPI stays because the user has implemented and can explain a real service
  with it; a skills line signals working experience, not expert-level mastery.
- Next.js and AWS are intentionally omitted from the base resume because the
  current depth is not strong enough to invite interview probing.
- Google Cloud, Modal, Redis, and Claude Agent SDK are supported by the
  AlphaResearch runtime; Azure is supported by the Northhealth deployment;
  PyTorch is supported by both research entries.
- Broad concepts such as multi-agent orchestration, LLM tooling, and RAG are
  intentionally omitted in favor of named, verifiable technologies.
