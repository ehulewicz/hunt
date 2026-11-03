// The only place the browser talks to a device's agent, over the tailnet. Like
// api.ts for Cloud Run, it is the one fetch and WebSocket layer for devices: it
// takes a fresh Supabase access token on every connect, and it refuses any
// address that is not a device's own https://<name>.ts.net, because the token
// goes with every request.

import type { Device } from "@/lib/api";
import { getSupabase } from "@/lib/supabase";

export type Span = { text: string; style?: string[]; link?: string };
export type LogLine = Span[];

export type Gap = {
  question: string;
  cause: string;
  required: boolean;
  note: string;
  fact_key: string | null;
  missing_fact: string | null;
  label: string | null;
  value_type: string | null;
  settable: boolean;
  hint?: string;
};

export type Fix = {
  key: string;
  question: string;
  cause: string;
  state: "queued" | "writing" | "testing" | "waiting" | "probing" | "kept" | "unsolved" | "missing_fact" | "later" | "paused";
  attempt: number;
  note: string | null;
};

export type RunSnapshot = {
  key: string;
  origin: "page" | "terminal";
  reference: string | null;
  phase: "updating" | "starting" | "running" | "ended";
  note: string | null;
  run_id: string | null;
  company: string | null;
  title: string | null;
  remote: boolean;
  outcome: string | null;
  letter: string | null;
  gaps: Gap[];
  fixes: Fix[];
  tab: string | null;
};

export type AgentDevice = {
  id: string;
  name: string;
  commit: string | null;
  local_changes: boolean | null;
  ready: boolean;
  problem: string | null;
};

export type Tab = { id: string; title: string; url: string };

export type AgentMessage =
  | { type: "hello"; device: AgentDevice; run: RunSnapshot | null; log: LogLine[] }
  | { type: "run"; run: RunSnapshot | null }
  | { type: "log"; lines: LogLine[] }
  | { type: "tabs"; tabs: Tab[]; current: string | null }
  | { type: "letter"; text: string }
  | { type: "gaps"; items: Gap[] }
  | { type: "fixes"; items: Fix[] }
  | { type: "fact_result"; question: string; ok: boolean; message: string }
  | { type: "stream"; state: "unavailable" | "lost"; message?: string };

export type FrameHeader = { type: "frame"; target: string | null; width: number; height: number };
export type View = { width: number; height: number; dpr: number };

// Close codes the agent uses.
export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_REPLACED = 4000;
export const CLOSE_RESTARTING = 1012;

const TAILNET = /^https:\/\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.ts\.net$/;

export class DeviceUnreachable extends Error {}

export class DeviceRequestError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export function deviceOrigin(device: Pick<Device, "url">): string {
  const url = device.url.replace(/\/$/, "");
  if (!TAILNET.test(url)) throw new Error("A device address must be https://<name>.ts.net");
  return url;
}

export async function freshAccessToken(): Promise<string> {
  const { data, error } = await getSupabase().auth.getSession();
  if (error || !data.session) throw new Error("Sign in again to reach your devices");
  return data.session.access_token;
}

export async function deviceRequest<T>(
  device: Pick<Device, "url">,
  path: string,
  init: RequestInit = {},
  token: () => Promise<string> = freshAccessToken,
): Promise<T> {
  const origin = deviceOrigin(device);
  let response: Response;
  try {
    response = await fetch(`${origin}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${await token()}`,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
    });
  } catch {
    throw new DeviceUnreachable("The device did not answer");
  }
  if (!response.ok) {
    let message = `The device answered ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // the status says enough
    }
    throw new DeviceRequestError(response.status, message);
  }
  return (await response.json()) as T;
}

export function startRun(
  device: Pick<Device, "url">,
  reference: string | null,
  fresh: boolean,
  token?: () => Promise<string>,
): Promise<{ run: RunSnapshot }> {
  return deviceRequest(device, "/runs", {
    method: "POST",
    body: JSON.stringify({ reference, fresh }),
  }, token);
}

// One binary message: a 4-byte big-endian length, the JSON header, then the JPEG.
export function parseFrame(buffer: ArrayBuffer): { header: FrameHeader; jpeg: Uint8Array } {
  const size = new DataView(buffer).getUint32(0);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, size))) as FrameHeader;
  return { header, jpeg: new Uint8Array(buffer, 4 + size) };
}

type Keyish = Pick<KeyboardEvent, "key" | "code" | "location" | "repeat" | "ctrlKey" | "altKey" | "metaKey" | "shiftKey">;

// The DOM's own key fields; the agent turns them into Chrome key events.
export function keyMessage(event: Keyish, kind: "down" | "up") {
  return {
    type: "key",
    event: kind,
    key: event.key,
    code: event.code,
    location: event.location,
    repeat: event.repeat,
    ctrl: event.ctrlKey,
    alt: event.altKey,
    meta: event.metaKey,
    shift: event.shiftKey,
  } as const;
}

type Pointerish = Pick<MouseEvent, "clientX" | "clientY" | "button" | "buttons" | "detail" | "ctrlKey" | "altKey" | "metaKey" | "shiftKey">;
type Box = Pick<DOMRect, "left" | "top" | "width" | "height">;

// A position on the shown frame, as fractions of it, so no pixel math happens here.
export function pointerMessage(event: Pointerish, box: Box, kind: "move" | "down" | "up") {
  return {
    type: "pointer",
    event: kind,
    x: box.width ? (event.clientX - box.left) / box.width : 0,
    y: box.height ? (event.clientY - box.top) / box.height : 0,
    button: event.button,
    buttons: event.buttons,
    clickCount: Math.max(1, event.detail || 1),
    ctrl: event.ctrlKey,
    alt: event.altKey,
    meta: event.metaKey,
    shift: event.shiftKey,
  } as const;
}

export function wheelMessage(event: Pick<WheelEvent, "clientX" | "clientY" | "deltaX" | "deltaY" | "deltaMode" | "ctrlKey" | "altKey" | "metaKey" | "shiftKey">, box: Box) {
  return {
    type: "wheel",
    x: box.width ? (event.clientX - box.left) / box.width : 0,
    y: box.height ? (event.clientY - box.top) / box.height : 0,
    dx: event.deltaX,
    dy: event.deltaY,
    mode: event.deltaMode,
    ctrl: event.ctrlKey,
    alt: event.altKey,
    meta: event.metaKey,
    shift: event.shiftKey,
  } as const;
}

export type SessionHandlers = {
  onOpen: () => void;
  onMessage: (message: AgentMessage) => void;
  onFrame: (header: FrameHeader, jpeg: Blob) => void;
  onClose: (code: number, opened: boolean) => void;
};

export type DeviceSession = { send: (message: object) => void; close: () => void };

// The live session: the token goes first, then frames and messages flow both ways.
export function openDeviceSession(
  device: Pick<Device, "url">,
  view: View,
  handlers: SessionHandlers,
  token: () => Promise<string> = freshAccessToken,
  Socket: typeof WebSocket = WebSocket,
): DeviceSession {
  const address = `${deviceOrigin(device).replace(/^https:/, "wss:")}/session`;
  let socket: WebSocket | null = null;
  let closed = false;
  let opened = false;
  void token().then(
    (accessToken) => {
      if (closed) return;
      socket = new Socket(address);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        opened = true;
        socket?.send(JSON.stringify({ type: "auth", token: accessToken, view }));
        handlers.onOpen();
      };
      socket.onmessage = (event: MessageEvent) => {
        if (typeof event.data === "string") {
          handlers.onMessage(JSON.parse(event.data) as AgentMessage);
          return;
        }
        const { header, jpeg } = parseFrame(event.data as ArrayBuffer);
        handlers.onFrame(header, new Blob([jpeg as BlobPart], { type: "image/jpeg" }));
      };
      socket.onclose = (event: CloseEvent) => {
        if (!closed) handlers.onClose(event.code, opened);
      };
    },
    () => handlers.onClose(CLOSE_UNAUTHORIZED, false),
  );
  return {
    send(message) {
      if (socket?.readyState === Socket.OPEN) socket.send(JSON.stringify(message));
    },
    close() {
      closed = true;
      socket?.close(1000);
    },
  };
}

export type AssistRequest = {
  surface: "resume" | "library" | "cover_letter";
  instruction: string;
  document: string;
};

export type AssistResponse = { proposal: string; summary: string };

// A bounded model call run on the device itself; it can take minutes. The
// caller shows the returned proposal for review; nothing is saved here.
export function askClaude(
  device: Pick<Device, "url">,
  request: AssistRequest,
  token?: () => Promise<string>,
): Promise<AssistResponse> {
  return deviceRequest<AssistResponse>(device, "/assist", {
    method: "POST",
    body: JSON.stringify(request),
  }, token);
}
