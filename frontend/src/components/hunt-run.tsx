"use client";

import { type FormEvent, memo, useState } from "react";

import type { Gap, LogLine, RunSnapshot, Span } from "@/lib/device";

const CONTROLS = [
  { action: "save", label: "Save", help: "Record the answers picked up so far" },
  { action: "done", label: "Submitted", help: "You submitted it: record it as applied" },
  { action: "skip", label: "Skip", help: "Pass on this listing; it leaves the board" },
  { action: "stop", label: "Stop", help: "Stop and record the form as it stands" },
] as const;

const VALUE_TYPES = ["text", "boolean", "number", "date", "choice", "list", "object"];
const STYLES = new Set(["bold", "dim", "italic", "underline", "red", "green", "yellow", "blue", "magenta", "cyan"]);

export type FactAnswer = { question: string; key: string; label: string; value_type: string; value: string };

function LogSpan({ span }: { span: Span }) {
  const className = (span.style ?? []).filter((name) => STYLES.has(name)).map((name) => `log-${name}`).join(" ") || undefined;
  if (span.link && /^https?:\/\//.test(span.link)) return <a className={className} href={span.link} rel="noreferrer" target="_blank">{span.text}</a>;
  return <span className={className}>{span.text}</span>;
}

// A line never changes once logged, so a new line renders one row, not thousands.
const LogRow = memo(function LogRow({ line }: { line: LogLine }) {
  return <li>{line.length ? line.map((span, part) => <LogSpan key={part} span={span} />) : " "}</li>;
});

export function RunLog({ lines }: { lines: LogLine[] }) {
  if (lines.length === 0) return <p className="muted run-empty">Nothing has run on this device since its agent started.</p>;
  return (
    <ol aria-label="Run log" className="run-log">
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

function GapForm({ gap, result, onFact }: { gap: Gap; result?: { ok: boolean; message: string }; onFact: (answer: FactAnswer) => void }) {
  const [key, setKey] = useState(gap.fact_key ?? "");
  const [label, setLabel] = useState(gap.label ?? "");
  const [valueType, setValueType] = useState(gap.value_type ?? "text");
  const [value, setValue] = useState("");
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
        <label>Fact key<input onChange={(event) => setKey(event.target.value)} placeholder="work_auth.clearance" value={key} /></label>
      )}
      {!known && (
        <>
          <label>Label<input onChange={(event) => setLabel(event.target.value)} value={label} /></label>
          <label>Type
            <select onChange={(event) => setValueType(event.target.value)} value={valueType}>
              {VALUE_TYPES.map((type) => <option key={type}>{type}</option>)}
            </select>
          </label>
        </>
      )}
      <label>{known ? gap.label : "Value"}
        {valueType === "boolean" ? (
          <select onChange={(event) => setValue(event.target.value)} value={value}>
            <option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option>
          </select>
        ) : (
          <input onChange={(event) => setValue(event.target.value)} type={valueType === "date" ? "date" : valueType === "number" ? "number" : "text"} value={value} />
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
  results,
  starting,
  startError,
  onStart,
  onControl,
  onLetter,
  onFact,
}: {
  deviceName: string;
  connected: boolean;
  run: RunSnapshot | null;
  log: LogLine[];
  letter: string | null;
  gaps: Gap[];
  results: Record<string, { ok: boolean; message: string }>;
  starting: boolean;
  startError: string | null;
  onStart: (reference: string | null, fresh: boolean) => void;
  onControl: (action: string) => void;
  onLetter: (text: string) => void;
  onFact: (answer: FactAnswer) => void;
}) {
  const [reference, setReference] = useState("");
  const [fresh, setFresh] = useState(false);
  const [edited, setEdited] = useState<{ draft: string; text: string } | null>(null);
  const active = run !== null && run.phase !== "ended";
  const linked = active && run.phase === "running";
  const text = edited && edited.draft === letter ? edited.text : letter ?? "";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (reference.trim()) onStart(reference.trim(), fresh);
  }

  return (
    <section aria-label="Run" className="run-panel">
      <form aria-label={`Start a run on ${deviceName}`} className="run-start" onSubmit={submit}>
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
          <button className="button button-secondary" disabled={!connected || active || starting} onClick={() => onStart(null, fresh)} type="button">Next on board</button>
        </div>
        {startError && <p className="gap-error" role="alert">{startError}</p>}
      </form>

      {run && (
        <div aria-label="Current run" className="run-status" role="group">
          <div className="run-heading">
            <strong>{run.company ? `${run.company} — ${run.title ?? ""}` : run.reference ? `Reference ${run.reference}` : "Top of the board"}</strong>
            <span className={run.phase === "ended" ? "status-pill status-pill-muted" : "status-pill"}>{phaseLabel(run)}</span>
          </div>
          {run.origin === "terminal" && <p className="muted">Started in a terminal on {deviceName}; the controls work here too.</p>}
          {run.note && <p className="muted">{run.note}</p>}
          <div className="run-controls">
            {CONTROLS.map((control) => (
              <button
                className={control.action === "stop" ? "button button-secondary run-stop" : "button button-secondary"}
                disabled={control.action === "stop" ? !active : !linked}
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

      {letter !== null && (
        <form
          aria-label="Cover letter"
          className="letter-editor"
          onSubmit={(event) => { event.preventDefault(); onLetter(text); }}
        >
          <label htmlFor="cover-letter"><strong>Cover letter</strong> <span className="muted">The run waits for it.</span></label>
          <textarea id="cover-letter" onChange={(event) => setEdited({ draft: letter, text: event.target.value })} rows={16} value={text} />
          <button className="button button-primary" type="submit">Use this letter</button>
        </form>
      )}

      {gaps.length > 0 && (
        <section aria-label="Left for you" className="gap-list">
          <h2>Left for you</h2>
          {gaps.map((gap) => (
            <div className="gap-row" key={gap.question}>
              <div className="gap-question">
                <strong>{gap.question}</strong>
                <span className={gap.required ? "status-pill status-pill-warning" : "status-pill status-pill-muted"}>{gap.required ? "Required" : "Optional"}</span>
              </div>
              <p className="muted">{gap.cause.replace(/_/g, " ")}{gap.note ? `: ${gap.note}` : ""}{gap.hint ? ` (${gap.hint})` : ""}</p>
              {gap.settable && <GapForm gap={gap} onFact={onFact} result={results[gap.question]} />}
            </div>
          ))}
        </section>
      )}

      <RunLog lines={log} />
    </section>
  );
});
