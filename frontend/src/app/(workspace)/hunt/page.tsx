"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { defaultDevice, HuntDevices } from "@/components/hunt-devices";
import { HuntLive, viewOf } from "@/components/hunt-live";
import { type FactAnswer, HuntRun } from "@/components/hunt-run";
import { useDeviceSession } from "@/components/use-device-session";
import { type Device, listDevices } from "@/lib/api";
import { DeviceUnreachable, freshAccessToken, startRun } from "@/lib/device";

const REFRESH_MS = 30_000;

function HuntSession({ device }: { device: Device }) {
  const live = useRef<HTMLDivElement>(null);
  const { state, send, retry } = useDeviceSession(device.url, () => viewOf(live.current));
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // Stable, so the memoized run panel does not re-render on every frame.
  const onStart = useCallback((reference: string | null, fresh: boolean) => {
    setStarting(true);
    setStartError(null);
    startRun(device, reference, fresh)
      .catch((caught: unknown) => setStartError(
        caught instanceof DeviceUnreachable
          ? `Can't reach ${device.name}. Is this browser on your tailnet?`
          : caught instanceof Error ? caught.message : "The run did not start",
      ))
      .finally(() => setStarting(false));
  }, [device]);
  const onControl = useCallback((action: string) => send({ type: "control", action }), [send]);
  const onFact = useCallback((answer: FactAnswer) => send({ type: "fact", ...answer }), [send]);
  const onLetter = useCallback((text: string) => send({ type: "letter", text }), [send]);

  return (
    <div className="hunt-layout">
      <HuntRun
        connected={state.status === "open"}
        deviceName={device.name}
        gaps={state.gaps}
        letter={state.letter}
        log={state.log}
        onControl={onControl}
        onFact={onFact}
        onLetter={onLetter}
        onStart={onStart}
        results={state.results}
        run={state.run}
        startError={startError}
        starting={starting}
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
  );
}

export default function HuntPage() {
  const [listed, setListed] = useState<{ devices: Device[]; at: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const devices = await listDevices(await freshAccessToken());
        if (active) {
          const items = Array.isArray(devices) ? devices : [];
          setListed({ devices: items, at: Date.now() });
          // Once chosen, a device keeps its session: a few missed heartbeats do not
          // tear down a live view that is still connected over the tailnet.
          setChosen((current) => current ?? defaultDevice(items));
          setError(null);
        }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : "Could not list your devices");
      }
    }
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const selected = listed?.devices.find((device) => device.id === chosen) ?? null;

  return (
    <>
      <header className="page-header">
        <p className="eyebrow">Jobs</p>
        <h1>Hunt</h1>
        <p>Start and drive applications on one of your devices, with its Chrome beside the run.</p>
      </header>
      {error && <p className="auth-notice" role="alert">{error}</p>}
      {listed === null
        ? <div aria-label="Loading devices" className="inline-loading"><span className="spinner" /></div>
        : <HuntDevices devices={listed.devices} now={listed.at} onSelect={setChosen} selectedId={selected?.id ?? null} />}
      {selected && <HuntSession device={selected} key={selected.id} />}
    </>
  );
}
