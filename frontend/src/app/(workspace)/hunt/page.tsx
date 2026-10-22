"use client";

import { ArrowLeft, ChevronDown, Columns2, Laptop, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { HuntDashboard } from "@/components/hunt-dashboard";
import { defaultDevice, HuntDevices } from "@/components/hunt-devices";
import { HuntLive, viewOf } from "@/components/hunt-live";
import { type FactAnswer, type GapDraft, HuntRun, type LetterDraft } from "@/components/hunt-run";
import { useDeviceSession } from "@/components/use-device-session";
import { type Device, type HuntOverview, listDevices, loadHuntOverview } from "@/lib/api";
import { DeviceUnreachable, freshAccessToken, type RunSnapshot, startRun } from "@/lib/device";

const REFRESH_MS = 30_000;

type Draft = LetterDraft | null;

function HuntSession({ device, balanced, draft, onDraft, gapDrafts, onGapDraft, onObserve, onRefresh }: {
  device: Device; balanced: boolean; draft: Draft; onDraft: (value: Draft) => void;
  gapDrafts: Record<string, GapDraft>; onGapDraft: (key: string, draft: GapDraft) => void;
  onObserve: (deviceId: string, run: RunSnapshot) => void; onRefresh: () => void;
}) {
  const live = useRef<HTMLDivElement>(null);
  const { state, send, retry } = useDeviceSession(device.url, () => viewOf(live.current));
  const connected = state.status === "open";
  const onControl = useCallback((action: string) => send({ type: "control", action }), [send]);
  const onFact = useCallback((answer: FactAnswer) => send({ type: "fact", ...answer }), [send]);
  const onLetter = useCallback((text: string) => send({ type: "letter", text }), [send]);

  useEffect(() => {
    if (state.run) onObserve(device.id, state.run);
    if (state.run?.phase === "ended") onRefresh();
  }, [device.id, state.run, onObserve, onRefresh]);

  return (
    <>
      <div className={balanced ? "hunt-layout balanced" : "hunt-layout"}>
        <HuntRun
          connected={connected}
          deviceName={device.name}
          draft={draft}
          gapDrafts={gapDrafts}
          gaps={state.gaps}
          fixes={state.fixes}
          letter={state.letter}
          log={state.log}
          onControl={onControl}
          onDraft={onDraft}
          onFact={onFact}
          onGapDraft={onGapDraft}
          onLetter={onLetter}
          results={state.results}
          run={state.run}
          workspace
        />
        <div className="hunt-live-column" ref={live}>
          <HuntLive
            currentTab={state.currentTab}
            deviceName={device.name}
            frame={state.frame}
            onRetry={retry}
            send={send}
            status={state.status}
            stream={state.stream}
            streamMessage={state.streamMessage}
            tabs={state.tabs}
          />
        </div>
      </div>
      <footer className="session-footer"><span><span className={connected ? "status-dot" : "status-dot offline"} />{connected ? "Connected to" : state.status === "connecting" || state.status === "restarting" ? "Connecting to" : "Disconnected from"} {device.name}</span><span>Chrome responds to clicks, typing, and scrolling.</span></footer>
    </>
  );
}

export default function HuntPage() {
  const [listed, setListed] = useState<{ devices: Device[]; at: number } | null>(null);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [overview, setOverview] = useState<HuntOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [session, setSession] = useState<Device | null>(null);
  const [balanced, setBalanced] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [gapDrafts, setGapDrafts] = useState<Record<string, Record<string, GapDraft>>>({});
  const [observed, setObserved] = useState<Record<string, { run: RunSnapshot; at: number }>>({});
  const startingRef = useRef(false);
  const picker = useRef<HTMLDetailsElement>(null);
  const refresh = useCallback(() => setVersion((current) => current + 1), []);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const token = await freshAccessToken();
        const [devices, data] = await Promise.allSettled([listDevices(token), loadHuntOverview(token)]);
        if (!active) return;
        if (devices.status === "fulfilled") {
          const items = Array.isArray(devices.value) ? devices.value : [];
          setListed({ devices: items, at: Date.now() });
          setChosen((current) => items.some((item) => item.id === current) ? current : defaultDevice(items));
          setDeviceError(null);
        } else setDeviceError(devices.reason instanceof Error ? devices.reason.message : "Could not list your devices");
        if (data.status === "fulfilled") {
          setOverview(data.value);
          setOverviewError(null);
        } else setOverviewError(data.reason instanceof Error ? data.reason.message : "Could not load your overview");
      } catch (caught) {
        if (active) setDeviceError(caught instanceof Error ? caught.message : "Could not load Hunt");
      }
    }
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => { active = false; clearInterval(timer); };
  }, [version]);

  // The session can know a run ended before the next hosted device heartbeat.
  // Never clear another run: reconcile by the exact run id, not company/title.
  const devices = (listed?.devices ?? []).map((device) => {
    const known = observed[device.id];
    if (!known) return device;
    if (known.run.phase === "ended") return device.active_run_id === known.run.run_id
      ? { ...device, active_run_id: null, run: null } : device;
    if (Date.parse(device.last_seen_at) > known.at) return device;
    return { ...device, active_run_id: known.run.run_id ?? known.run.key, run: known.run.company && known.run.title ? { company_name: known.run.company, title: known.run.title } : device.run };
  });
  const selected = devices.find((device) => device.id === chosen) ?? null;
  const current = session ? devices.find((device) => device.id === session.id) ?? session : selected;

  const onStart = useCallback((reference: string | null, fresh: boolean) => {
    if (!selected || startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    setStartError(null);
    void startRun(selected, reference, fresh)
      .then(({ run }) => {
        if (run) setObserved((current) => ({ ...current, [selected.id]: { run, at: Date.now() } }));
        setSession(selected);
        refresh();
      })
      .catch((caught: unknown) => setStartError(caught instanceof DeviceUnreachable
        ? `Can't reach ${selected.name}. Is this browser on your tailnet?`
        : caught instanceof Error ? caught.message : "The application did not start. Try again."))
      .finally(() => { startingRef.current = false; setStarting(false); });
  }, [selected, refresh]);

  const onDraft = useCallback((value: Draft) => {
    if (session) setDrafts((current) => ({ ...current, [session.id]: value }));
  }, [session]);
  const onGapDraft = useCallback((key: string, draft: GapDraft) => {
    if (session) setGapDrafts((current) => ({ ...current, [session.id]: { ...current[session.id], [key]: draft } }));
  }, [session]);
  const onObserve = useCallback((deviceId: string, run: RunSnapshot) => {
    setObserved((current) => ({ ...current, [deviceId]: { run, at: Date.now() } }));
  }, []);

  function openApplication(device: Device) {
    setChosen(device.id);
    setStartError(null);
    setSession(device);
  }

  return (
    <div className="hunt-page">
      <header className="hunt-topbar">
        <div className="hunt-location"><h1>Hunt</h1><span className="location-divider">/</span>{session ? <button aria-label="Back to overview" className="text-button" onClick={() => { setSession(null); refresh(); }} type="button"><ArrowLeft aria-hidden="true" />Overview</button> : <span>Overview</span>}</div>
        <div className="hunt-toolbar">
          {session && <button aria-label={balanced ? "Use 33/67 layout" : "Use 50/50 layout"} aria-pressed={balanced} className="layout-toggle" onClick={() => setBalanced(!balanced)} title="Change the width of the answer panel" type="button"><Columns2 aria-hidden="true" /><span>{balanced ? "50 / 50" : "33 / 67"}</span></button>}
          <details className="device-switcher" ref={picker}>
            <summary><span className={current?.online ? "status-dot" : "status-dot offline"} /><Laptop aria-hidden="true" /><span>{current?.name ?? "Choose device"}</span><ChevronDown aria-hidden="true" /></summary>
            <div className="device-popover"><h2>Your devices</h2><p className="muted">{session ? "Return to the overview to start on another device." : "Choose where your applications run."}</p>
              {listed ? <HuntDevices devices={devices} now={listed.at} onSelect={(id) => { setChosen(id); setStartError(null); setSession(null); if (picker.current) picker.current.open = false; }} selectedId={current?.id ?? null} /> : <p className="muted">Loading devices…</p>}
            </div>
          </details>
          {!session && <button aria-label="Refresh overview" className="icon-button" onClick={refresh} type="button"><RefreshCw aria-hidden="true" /></button>}
        </div>
      </header>
      {deviceError && <p className="hunt-notice" role="alert">{deviceError}<button className="text-button" onClick={refresh} type="button">Retry</button></p>}
      {overviewError && !session && <p className="hunt-notice" role="alert">{overviewError}. {overview ? "Showing the last loaded overview." : "Overview unavailable. You can still start an application."}<button className="text-button" onClick={refresh} type="button">Retry overview</button></p>}
      {session ? <HuntSession balanced={balanced} device={session} draft={drafts[session.id] ?? null} gapDrafts={gapDrafts[session.id] ?? {}} key={session.id} onDraft={onDraft} onGapDraft={onGapDraft} onObserve={onObserve} onRefresh={refresh} /> : <>
        <HuntDashboard device={selected} devices={devices} onOpen={openApplication} onRefresh={refresh} onStart={onStart} overview={overview} startError={startError} starting={starting} />
        <footer className="dashboard-footer"><span>{listed === null ? "Finding your devices…" : listed.devices.length === 0 ? <>Set up a device with <code>os device install</code> to get started.</> : current?.online ? <><span className="status-dot" />{current.ready ? "Ready when you are" : "Device needs attention"}</> : "No online device. Start your device agent to continue."}</span><span>{current?.local_changes ? `Running local changes at ${current.commit.slice(0, 7)}` : "Your workspace stays in sync"}</span></footer>
      </>}
    </div>
  );
}
