"use client";

import { Laptop } from "lucide-react";

import type { Device } from "@/lib/api";

function lastSeen(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 120) return "a minute ago";
  if (seconds < 7200) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} hours ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

// The device a session starts on: a ready one if any is online, else any online one.
export function defaultDevice(devices: Device[]): string | null {
  const online = devices.filter((device) => device.online);
  return (online.find((device) => device.ready) ?? online[0])?.id ?? null;
}

export function HuntDevices({
  devices,
  selectedId,
  onSelect,
  now,
}: {
  devices: Device[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  now: number; // when the list was fetched, which "last seen" counts from
}) {
  if (devices.length === 0) {
    return (
      <section aria-label="Devices" className="empty-state">
        <Laptop aria-hidden="true" />
        <h2>No devices yet</h2>
        <p>Run <code>os device install</code> in WSL on a Windows machine that runs Tailscale. It appears here once its agent is up.</p>
      </section>
    );
  }
  return (
    <section aria-label="Devices" className="status-list device-list">
      {devices.map((device) => {
        const commit = device.commit.slice(0, 7);
        const selected = device.id === selectedId;
        return (
          <div className={selected ? "device-row selected" : "device-row"} key={device.id}>
            <button
              aria-pressed={selected}
              className="device-pick"
              disabled={!device.online}
              onClick={() => onSelect(device.id)}
              type="button"
            >
              <strong>{device.name}</strong>
              <span>{device.online ? "Online" : `Last seen ${lastSeen(device.last_seen_at, now)}`}</span>
            </button>
            <span className={device.online ? "status-pill" : "status-pill status-pill-muted"}>
              {device.online ? "Online" : "Offline"}
            </span>
            <span className="device-detail">
              {device.ready ? "Ready" : <span className="device-problem">{device.problem}</span>}
            </span>
            <span className="device-detail">
              {device.local_changes
                ? <span className="status-pill status-pill-warning">Running local changes at {commit}</span>
                : <code>{commit}</code>}
            </span>
            <span className="device-detail">
              {device.run
                ? <>Busy: {device.run.company_name} — {device.run.title}</>
                : device.active_run_id ? "Busy" : "Idle"}
            </span>
          </div>
        );
      })}
    </section>
  );
}
