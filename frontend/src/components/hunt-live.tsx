"use client";

import { useEffect, useRef, useState } from "react";

import type { Frame, SessionStatus } from "@/components/use-device-session";
import { keyMessage, pointerMessage, type Tab, type View, wheelMessage } from "@/lib/device";

export function viewOf(element: HTMLElement | null): View {
  const width = element?.clientWidth || 960;
  return { width, height: Math.round(width * 0.625), dpr: typeof window === "undefined" ? 1 : window.devicePixelRatio || 1 };
}

function statusMessage(status: SessionStatus, deviceName: string): string | null {
  if (status === "connecting") return `Connecting to ${deviceName} …`;
  if (status === "unreachable") return `Can't reach ${deviceName}. Is this browser on your tailnet?`;
  if (status === "unauthorized") return `${deviceName} did not accept this session. Sign in again, then retry.`;
  if (status === "replaced") return `${deviceName} is open in another window.`;
  if (status === "restarting") return `${deviceName} is restarting with new code …`;
  if (status === "closed") return `Lost the connection to ${deviceName}; reconnecting …`;
  return null;
}

export function HuntLive({
  deviceName,
  status,
  tabs,
  currentTab,
  frame,
  stream,
  streamMessage,
  send,
  onRetry,
}: {
  deviceName: string;
  status: SessionStatus;
  tabs: Tab[];
  currentTab: string | null;
  frame: Frame | null;
  stream: "waiting" | "live" | "unavailable" | "lost";
  streamMessage: string | null;
  send: (message: object) => void;
  onRetry: () => void;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [focused, setFocused] = useState(false);
  const moving = useRef<ReturnType<typeof pointerMessage> | null>(null);

  // Wheel turns scroll the device's page, not this one, so the listener cannot be passive.
  useEffect(() => {
    const element = surface.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!image.current) return;
      event.preventDefault();
      send(wheelMessage(event, image.current.getBoundingClientRect()));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [send]);

  // The frame is sized to the pane, so a resized pane asks for a resized stream.
  useEffect(() => {
    const element = surface.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new ResizeObserver(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => send({ type: "view", ...viewOf(element) }), 300);
    });
    observer.observe(element);
    return () => { observer.disconnect(); if (timer) clearTimeout(timer); };
  }, [send]);

  function pointer(event: React.MouseEvent, kind: "move" | "down" | "up") {
    if (!image.current) return;
    const message = pointerMessage(event, image.current.getBoundingClientRect(), kind);
    if (kind !== "move") {
      send(message);
      return;
    }
    // At most one move per animation frame: the latest one.
    const pending = moving.current;
    moving.current = message;
    if (!pending) requestAnimationFrame(() => { if (moving.current) send(moving.current); moving.current = null; });
  }

  function key(event: React.KeyboardEvent, kind: "down" | "up") {
    // Ctrl+V or Cmd+V pastes this computer's clipboard; the paste event sends it.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") return;
    event.preventDefault();
    send(keyMessage(event.nativeEvent, kind));
  }

  const notice = statusMessage(status, deviceName);
  const current = tabs.find((tab) => tab.id === currentTab);
  return (
    <section aria-label="Live view" className="live-view">
      <div aria-label="Chrome tabs" className="tab-strip" role="toolbar">
        {tabs.map((tab) => (
          <button
            aria-pressed={tab.id === currentTab}
            className={tab.id === currentTab ? "tab-button active" : "tab-button"}
            key={tab.id}
            onClick={() => send({ type: "tab", id: tab.id })}
            title={tab.url}
            type="button"
          >
            {tab.title || tab.url || "Untitled"}
          </button>
        ))}
      </div>
      <div
        aria-label={`Chrome on ${deviceName}${current ? `: ${current.title}` : ""}. Click to control.`}
        className={focused ? "live-surface focused" : "live-surface"}
        onBlur={() => setFocused(false)}
        onContextMenu={(event) => event.preventDefault()}
        onFocus={() => setFocused(true)}
        onKeyDown={(event) => key(event, "down")}
        onKeyUp={(event) => key(event, "up")}
        onMouseDown={(event) => { surface.current?.focus(); pointer(event, "down"); }}
        onMouseMove={(event) => pointer(event, "move")}
        onMouseUp={(event) => pointer(event, "up")}
        onPaste={(event) => {
          event.preventDefault();
          const text = event.clipboardData.getData("text/plain");
          if (text) send({ type: "text", text });
        }}
        ref={surface}
        role="application"
        tabIndex={0}
      >
        {notice ? (
          <div className="live-notice" role="status">
            <p>{notice}</p>
            {(status === "unreachable" || status === "unauthorized" || status === "replaced") && (
              <button className="button button-secondary" onClick={onRetry} type="button">Try again</button>
            )}
          </div>
        ) : frame ? (
          // eslint-disable-next-line @next/next/no-img-element -- frames are object URLs streamed from the device
          <img alt="" draggable={false} height={frame.height} ref={image} src={frame.url} width={frame.width} />
        ) : (
          <p className="live-waiting">{stream === "waiting" ? "Waiting for the first frame …" : streamMessage ?? "Chrome is not streaming."}</p>
        )}
        {!notice && frame && stream !== "live" && <p className="live-waiting">{streamMessage ?? "The stream stopped."}</p>}
      </div>
    </section>
  );
}
