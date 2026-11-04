"use client";

import { type FormEvent, memo, useLayoutEffect, useRef, useState } from "react";
import { CheckCheck, ChevronDown, FileText, ListChecks, Terminal } from "lucide-react";

import { DocumentEditor } from "@/components/document-editor";
import type { Fix, Gap, LogLine, RunSnapshot, Span } from "@/lib/device";

const CONTROLS = [
  { action: "save", label: "Save", help: "Record the answers picked up so far" },
  { action: "done", label: "Submitted", help: "You submitted it: record it as applied" },
  { action: "skip", label: "Skip", help: "Pass on this listing; it leaves the board" },
  { action: "stop", label: "Stop", help: "Stop and record the form as it stands" },
] as const;

const VALUE_TYPES = ["text", "boolean", "number", "date", "choice", "list", "object"];
const STYLES = new Set(["bold", "dim", "italic", "underline", "red", "green", "yellow", "blue", "magenta", "cyan"]);

export type FactAnswer = { question: string; key: string; label: string; value_type: string; value: string };
export type GapDraft = { key: string; label: string; valueType: string; value: string };
export type LetterDraft = { runKey: string | null; draft: string; text: string };

function LogSpan({ span }: { span: Span }) {
  const className = (span.style ?? []).filter((name) => STYLES.has(name)).map((name) => `log-${name}`).join(" ") || undefined;
  if (span.link && /^https?:\/\//.test(span.link)) return <a className={className} href={span.link} rel="noreferrer" target="_blank">{span.text}</a>;
  return <span className={className}>{span.text}</span>;
}

// A line never changes once logged, so a new line renders one row, not thousands.
const LogRow = memo(function LogRow({ line }: { line: LogLine }) {
  return <li>{line.length ? line.map((span, part) => <LogSpan key={part} span={span} />) : " "}</li>;
});

// Within this many pixels of the bottom counts as reading the newest line.
const FOLLOW_SLACK = 24;

export function RunLog({ lines }: { lines: LogLine[] }) {
  const list = useRef<HTMLOListElement>(null);
  const following = useRef(true);
  // Like a terminal: new lines keep the newest in view, unless you scrolled up to read.
  useLayoutEffect(() => {
    const element = list.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [lines]);
  if (lines.length === 0) return <p className="muted run-empty">Nothing has run on this device since its agent started.</p>;
  return (
    <ol
      aria-label="Run log"
      className="run-log"
      onScroll={(event) => {
        const element = event.currentTarget;
        following.current = element.scrollHeight - element.scrollTop - element.clientHeight <= FOLLOW_SLACK;
      }}
      ref={list}
      tabIndex={0}
    >
      {lines.map((line, index) => <LogRow key={index} line={line} />)}
    </ol>
  );
}

function phaseLabel(run: RunSnapshot): string {
  if (run.phase === "updating") return "Updating the checkout";
  if (run.phase === "starting") return "Starting";
  if (run.phase === "running") return "Running";
  return run.outcome ? `Ended: ${run.outcome}` : "Ended";
}

// A direction joins the fixer's next attempts at this problem; what happens next shows on the row and the log.
function FixDirection({ fix, onGuide }: { fix: Fix; onGuide: (key: string, text: string) => void }) {
  const [text, setText] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  return (
    <form className="gap-form" onSubmit={(event) => {
      event.preventDefault();
      if (!text.trim()) return;
      onGuide(fix.key, text.trim());
      setSent(text.trim());
      setText("");
    }}>
      <label>Direction for the fixer<textarea onChange={(event) => setText(event.target.value)} rows={2} value={text} /></label>
      <button className="button" disabled={!text.trim()} type="submit">Send direction</button>
      {sent && <p className="muted" role="status">Sent: {sent}</p>}
    </form>
  );
}

function GapForm({ gap, result, onFact, draft, onDraft }: {
  gap: Gap; result?: { ok: boolean; message: string }; onFact: (answer: FactAnswer) => void;
  draft?: GapDraft; onDraft?: (draft: GapDraft) => void;
}) {
  const [local, setLocal] = useState<GapDraft>({ key: gap.fact_key ?? "", label: gap.label ?? "", valueType: gap.value_type ?? "text", value: "" });
  const values = draft ?? local;
  const { key, label, valueType, value } = values;
  function edit(changes: Partial<GapDraft>) {
    const updated = { ...values, ...changes };
    if (onDraft) onDraft(updated);
    else setLocal(updated);
  }
  const named = gap.fact_key !== null;
  const known = gap.label !== null;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!key.trim() || !value.trim()) return;
    onFact({ question: gap.question, key: key.trim(), label: label.trim(), value_type: valueType, value });
  }

  return (
    <form aria-label={`Fact for ${gap.question}`} className="gap-form" onSubmit={submit}>
      {!named && (
        <label>Fact key<input onChange={(event) => edit({ key: event.target.value })} placeholder="work_auth.clearance" value={key} /></label>
      )}
      {!known && (
        <>
          <label>Label<input onChange={(event) => edit({ label: event.target.value })} value={label} /></label>
          <label>Type
            <select onChange={(event) => edit({ valueType: event.target.value, value: "" })} value={valueType}>
              {VALUE_TYPES.map((type) => <option key={type}>{type}</option>)}
            </select>
          </label>
        </>
      )}
      <label>{known ? gap.label : "Value"}
        {valueType === "boolean" ? (
          <select onChange={(event) => edit({ value: event.target.value })} value={value}>
            <option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
        ) : (
          <input onChange={(event) => edit({ value: event.target.value })} type={valueType === "date" ? "date" : valueType === "number" ? "number" : "text"} value={value} />
        )}
      </label>
      <button className="button button-secondary" type="submit">Set and fill</button>
      {result && !result.ok && <p className="gap-error" role="alert">{result.message}</p>}
    </form>
  );
}

// Frames arrive many times a second; memo keeps them from re-rendering the run and its log.
export const HuntRun = memo(function HuntRun({
  deviceName,
  connected,
  run,
  log,
  letter,
  gaps,
  fixes = run?.fixes ?? [],
  results,
  starting,
  startError,
  onStart,
  onControl,
  onLetter,
  onFact,
  onGuide,
  workspace = false,
  draft,
  onDraft,
  gapDrafts,
  onGapDraft,
}: {
  deviceName: string;
  connected: boolean;
  run: RunSnapshot | null;
  log: LogLine[];
  letter: string | null;
  gaps: Gap[];
  fixes?: Fix[];
  results: Record<string, { ok: boolean; message: string }>;
  starting?: boolean;
  startError?: string | null;
  onStart?: (reference: string | null, fresh: boolean) => void;
  onControl: (action: string) => void;
  onLetter: (text: string) => void;
  onFact: (answer: FactAnswer) => void;
  onGuide?: (key: string, text: string) => void;
  workspace?: boolean;
  draft?: LetterDraft | null;
  onDraft?: (value: LetterDraft) => void;
  gapDrafts?: Record<string, GapDraft>;
  onGapDraft?: (key: string, draft: GapDraft) => void;
}) {
  const [reference, setReference] = useState("");
  const [fresh, setFresh] = useState(false);
  const [edited, setEdited] = useState<LetterDraft | null>(null);
  const [pane, setPane] = useState<"facts" | "letter">("facts");
  const active = run !== null && run.phase !== "ended";
  const linked = active && run.phase === "running";
  const currentDraft = onDraft ? draft : edited;
  const text = currentDraft && currentDraft.runKey === (run?.key ?? null) && currentDraft.draft === letter ? currentDraft.text : letter ?? "";

  function editLetter(value: string) {
    const next = { runKey: run?.key ?? null, draft: letter ?? "", text: value };
    if (onDraft) onDraft(next);
    else setEdited(next);
  }
  const showLetter = !workspace || pane === "letter" && letter !== null;
  const showGaps = !workspace || !showLetter;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (reference.trim()) onStart?.(reference.trim(), fresh);
  }

  return (
    <section aria-label="Run" className="run-panel">
      {!workspace && <form aria-label={`Start a run on ${deviceName}`} className="run-start" onSubmit={submit}>
        <label className="run-reference">
          <span>Apply to</span>
          <input
            disabled={!connected || active || starting}
            onChange={(event) => setReference(event.target.value)}
            placeholder="Board number, id prefix, or posting URL"
            value={reference}
          />
        </label>
        <div className="run-start-actions">
          <label className="run-fresh">
            <input checked={fresh} onChange={(event) => setFresh(event.target.checked)} type="checkbox" />
            New tab
          </label>
          <button className="button button-primary" disabled={!connected || active || starting || !reference.trim()} type="submit">Apply</button>
          <button className="button button-secondary" disabled={!connected || active || starting} onClick={() => onStart?.(null, fresh)} type="button">Next on board</button>
        </div>
        {startError && <p className="gap-error" role="alert">{startError}</p>}
      </form>}

      {run && (
        <div aria-label="Current run" className="run-status" role="group">
          <div className="run-heading">
            <span className={run.phase === "ended" ? "status-pill status-pill-muted" : "status-pill"}>{phaseLabel(run)}</span>
            <span>{deviceName}</span>
          </div>
          <div className="run-job-heading"><small>{run.company ?? "Your next application"}</small><strong>{run.title ?? (run.reference ? `Reference ${run.reference}` : "Next on the board")}</strong></div>
          {run.origin === "terminal" && <p className="muted">Started in a terminal on {deviceName}; the controls work here too.</p>}
          {run.note && <p className="muted">{run.note}</p>}
          <div className="run-controls">
            {CONTROLS.map((control) => (
              <button
                className={control.action === "stop" ? "button button-secondary run-stop" : "button button-secondary"}
                disabled={!connected || (control.action === "stop" ? !active : !linked)}
                key={control.action}
                onClick={() => onControl(control.action)}
                title={control.help}
                type="button"
              >
                {control.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {workspace && !run && <div className="run-connecting"><span className="spinner" /><strong>Opening your application</strong><p>The current run and its answers will appear here.</p></div>}

      {workspace && <div aria-label="Application panels" className="run-panel-tabs">
        <button aria-pressed={!showLetter} onClick={() => setPane("facts")} type="button"><ListChecks aria-hidden="true" />To finish<span>{gaps.length}</span></button>
        <button aria-pressed={showLetter} disabled={letter === null} onClick={() => setPane("letter")} type="button"><FileText aria-hidden="true" />Cover letter{letter !== null && <span className="draft-dot" />}</button>
      </div>}
      <div className="run-content">
      <div hidden={!showLetter}>
      {letter !== null && (
        <DocumentEditor
          claude={{
            surface: "cover_letter",
            onAccept: (proposal) => editLetter(proposal),
          }}
          disabled={!connected || !linked}
          hint="The run waits for it."
          label="Cover letter"
          onChange={editLetter}
          onSave={async (value) => { onLetter(value); }}
          saveLabel="Use this letter"
          value={text}
        />
      )}
      </div>

      <div hidden={!showGaps}>
      {fixes.length > 0 && <section aria-label="Live fixes" className="gap-list" aria-live="polite">
        <h2>Live fixes</h2>
        {fixes.map((fix) => <div className="gap-row" key={fix.key}>
          <div className="gap-question"><strong>{fix.question}</strong><span className={fix.state === "kept" || fix.state === "closed" ? "status-pill" : "status-pill status-pill-muted"}>{fix.state.replace(/_/g, " ")}</span></div>
          <p className="muted">{fix.cause.replace(/_/g, " ")} · <span>Attempt {fix.attempt > 3 ? fix.attempt : `${fix.attempt}/3`}</span></p>
          {fix.note && <p className="fix-note">{fix.note}</p>}
          {linked && connected && onGuide && fix.key !== "fixer" && fix.state !== "kept" && fix.state !== "closed" && <FixDirection fix={fix} onGuide={onGuide} />}
        </div>)}
      </section>}
      {gaps.length > 0 && (
        <section aria-label="Left for you" className="gap-list">
          <h2>Left for you</h2>
          {gaps.map((gap) => (
            <div className="gap-row" key={`${run?.key}:${gap.question}`}>
              <div className="gap-question">
                <strong>{gap.question}</strong>
                <span className={gap.required ? "status-pill status-pill-warning" : "status-pill status-pill-muted"}>{gap.required ? "Required" : "Optional"}</span>
              </div>
              <p className="muted">{gap.cause.replace(/_/g, " ")}{gap.note ? `: ${gap.note}` : ""}{gap.hint ? ` (${gap.hint})` : ""}</p>
              {gap.settable && <fieldset className="gap-fields" disabled={!connected || !linked}><GapForm draft={gapDrafts?.[`${run?.key}:${gap.question}`]} gap={gap} onDraft={onGapDraft ? (draft) => onGapDraft(`${run?.key}:${gap.question}`, draft) : undefined} onFact={onFact} result={results[gap.question]} /></fieldset>}
            </div>
          ))}
        </section>
      )}
      {workspace && showGaps && gaps.length === 0 && fixes.length === 0 && <div className="panel-empty run-clear"><CheckCheck aria-hidden="true" /><h3>{active ? "Nothing needs your input yet" : run ? "Application recorded" : "Getting things ready"}</h3><p>{active ? "Questions that need an answer will appear here. You can also work directly in Chrome." : run ? "Return to the overview to choose your next application." : "Your application will open beside this panel."}</p></div>}
      </div>
      </div>
      <details className="run-log-drawer" open={workspace ? undefined : true}>
        <summary><Terminal aria-hidden="true" />Run log<span>{log.length} lines</span><ChevronDown aria-hidden="true" /></summary>
        <RunLog lines={log} />
      </details>
    </section>
  );
});
