"use client";

import { type CSSProperties, useEffect, useRef, useState } from "react";
import { Globe2, Radio } from "lucide-react";

import type { Frame, SessionStatus } from "@/components/use-device-session";
import { keyMessage, pointerMessage, type Tab, type View, wheelMessage } from "@/lib/device";

// The agent lays the tab out at this size, so a frame fills the pane exactly.
export function viewOf(element: HTMLElement | null): View {
  const width = element?.clientWidth || 960;
  const height = element?.clientHeight || Math.round(width * 0.625);
  return { width, height, dpr: typeof window === "undefined" ? 1 : window.devicePixelRatio || 1 };
}

// The tab's layout shape, which the pane fits the frame to; without one it shows at its own size.
function frameSize(frame: Frame): CSSProperties | undefined {
  if (!(frame.width > 0 && frame.height > 0)) return undefined;
  return { "--frame-ratio": frame.width / frame.height } as CSSProperties;
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

  // Ask for the pane's view on connect, reconnect, and resize.
  useEffect(() => {
    const element = surface.current;
    if (!element || status !== "open") return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => send({ type: "view", ...viewOf(element) }), 300);
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(element);
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", schedule);
      if (timer) clearTimeout(timer);
    };
  }, [send, status]);

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
        {tabs.length === 0 && <span className="browser-tab-placeholder">Application Chrome</span>}
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
      <div className="browser-location"><Globe2 aria-hidden="true" /><span>{current?.url ?? deviceName}</span><span className={status === "open" && stream === "live" ? "browser-live" : "browser-state"}><Radio aria-hidden="true" />{status === "open" ? stream === "live" ? "Live" : "Waiting for Chrome" : status === "connecting" || status === "restarting" ? "Connecting" : "Disconnected"}</span></div>
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
          <img alt="" draggable={false} height={frame.height} ref={image} src={frame.url} style={frameSize(frame)} width={frame.width} />
        ) : (
          <p className="live-waiting">{stream === "waiting" ? "Waiting for the first frame …" : streamMessage ?? "Chrome is not streaming."}</p>
        )}
        {!notice && frame && stream !== "live" && <p className="live-waiting">{streamMessage ?? "The stream stopped."}</p>}
      </div>
    </section>
  );
}
