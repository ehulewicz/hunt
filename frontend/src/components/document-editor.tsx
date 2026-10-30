"use client";

import { Check, FileText, Sparkles } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";

import { listDevices, type Device } from "@/lib/api";
import {
  askClaude,
  DeviceUnreachable,
  freshAccessToken,
  type AssistRequest,
} from "@/lib/device";
import { diffLines, diffWords, type DiffPart } from "@/lib/diff";

export type ClaudeSurface = AssistRequest["surface"];

export type ClaudeAssist = {
  surface: ClaudeSurface;
  // Accept runs the caller's save path with the proposal; it may throw, and the
  // refusal shows inline.
  onAccept: (proposal: string) => void | Promise<void>;
  disabledReason?: string;
};

export function DocumentEditor({
  label,
  hint,
  value,
  onChange,
  onSave,
  saveLabel,
  monospace = false,
  disabled = false,
  footer,
  claude,
}: {
  label: string;
  hint?: ReactNode;
  value: string;
  onChange: (value: string) => void;
  // Returns the saved notice to show, or nothing for no notice. A thrown
  // error's message shows inline and nothing is marked saved.
  onSave: (value: string) => Promise<string | void>;
  saveLabel: string;
  monospace?: boolean;
  disabled?: boolean;
  footer?: ReactNode;
  claude?: ClaudeAssist;
}) {
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await onSave(value);
      if (typeof saved === "string" && saved) setNotice(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="document-editor">
      <label htmlFor={id}><strong>{label}</strong>{hint ? <span className="muted"> {hint}</span> : null}</label>
      <textarea
        className={monospace ? "editor-monospace" : undefined}
        id={id}
        onChange={(event) => { onChange(event.target.value); setError(null); setNotice(null); }}
        rows={monospace ? 24 : 8}
        value={value}
      />
      <div className="document-editor-actions">
        <button className="button button-primary" disabled={disabled || busy} onClick={() => void save()} type="button">
          <Check aria-hidden="true" />{busy ? "Saving…" : saveLabel}
        </button>
        {footer}
      </div>
      {notice && <p className="saved-notice" role="status"><Check aria-hidden="true" />{notice}</p>}
      {error && <p className="gap-error" role="alert">{error}</p>}
      {claude && (
        <AskClaudePanel
          current={value}
          disabledReason={claude.disabledReason}
          monospace={monospace}
          onAccept={claude.onAccept}
          surface={claude.surface}
        />
      )}
    </div>
  );
}

function AskClaudePanel({
  surface,
  current,
  onAccept,
  disabledReason,
  monospace = false,
}: {
  surface: ClaudeSurface;
  current: string;
  onAccept: (proposal: string) => void | Promise<void>;
  disabledReason?: string;
  monospace?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<{ text: string; summary: string } | null>(null);

  useEffect(() => {
    if (!open || devices !== null) return;
    let active = true;
    void (async () => {
      try {
        const list = await listDevices(await freshAccessToken());
        if (active) setDevices(list);
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Could not list your devices.");
      }
    })();
    return () => { active = false; };
  }, [open, devices]);

  const device = devices?.find((item) => item.online && item.ready) ?? null;
  const reason = disabledReason
    ?? (devices !== null && device === null
      ? "No device is online to run Claude; start one with os device serve."
      : null);
  const diff: DiffPart[] = proposal
    ? (monospace ? diffLines(current, proposal.text) : diffWords(current, proposal.text))
    : [];

  async function ask() {
    if (!device || busy) return;
    setBusy(true);
    setError(null);
    setProposal(null);
    try {
      const response = await askClaude(device, { surface, instruction: instruction.trim(), document: current });
      setProposal({ text: response.proposal, summary: response.summary });
    } catch (caught) {
      setError(
        caught instanceof DeviceUnreachable
          ? "The device did not answer. Check it is still online."
          : caught instanceof Error ? caught.message : "Claude could not be reached.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function accept() {
    if (!proposal) return;
    setError(null);
    try {
      await onAccept(proposal.text);
      setProposal(null);
      setInstruction("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the proposal.");
    }
  }

  return (
    <div className="claude-panel">
      <button
        aria-expanded={open}
        className="text-button"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <Sparkles aria-hidden="true" />Ask Claude
      </button>
      {open && (
        <div className="claude-body">
          <p className="muted">A revision proposal shows as a diff for you to accept or discard; nothing is saved on its own.</p>
          <label className="claude-instruction">
            <span className="sr-only">Instruction for Claude</span>
            <input
              disabled={busy || reason !== null}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="What should change?"
              value={instruction}
            />
          </label>
          <button
            className="button button-secondary"
            disabled={busy || reason !== null || device === null || !instruction.trim()}
            onClick={() => void ask()}
            type="button"
          >
            {busy ? "Asking…" : "Propose a revision"}
          </button>
          {reason && <p className="muted" role="note">{reason}</p>}
          {error && <p className="gap-error" role="alert">{error}</p>}
          {proposal && (
            <div className="claude-proposal">
              <p className="muted"><FileText aria-hidden="true" /> {proposal.summary}</p>
              <div aria-label="Proposed changes" className="editor-diff">
                {diff.map((part, index) => (
                  <span
                    className={part.kind === "added" ? "diff-added" : part.kind === "removed" ? "diff-removed" : undefined}
                    key={index}
                  >
                    {part.text}
                  </span>
                ))}
              </div>
              <div className="claude-actions">
                <button className="button button-secondary" onClick={() => void accept()} type="button">Accept</button>
                <button className="text-button" onClick={() => setProposal(null)} type="button">Discard</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
