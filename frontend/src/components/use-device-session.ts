"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import type { Device } from "@/lib/api";
import {
  type AgentDevice,
  type AgentMessage,
  CLOSE_REPLACED,
  CLOSE_RESTARTING,
  CLOSE_UNAUTHORIZED,
  type DeviceSession,
  type FrameHeader,
  type Gap,
  type LogLine,
  openDeviceSession,
  type RunSnapshot,
  type Tab,
  type View,
} from "@/lib/device";

export type SessionStatus =
  | "connecting"
  | "open"
  | "unreachable"
  | "unauthorized"
  | "replaced"
  | "restarting"
  | "closed";

export type Frame = { url: string; width: number; height: number; target: string | null };

export type SessionState = {
  status: SessionStatus;
  agent: AgentDevice | null;
  run: RunSnapshot | null;
  log: LogLine[];
  tabs: Tab[];
  currentTab: string | null;
  frame: Frame | null;
  stream: "waiting" | "live" | "unavailable" | "lost";
  streamMessage: string | null;
  letter: string | null;
  gaps: Gap[];
  results: Record<string, { ok: boolean; message: string }>;
};

const LOG_LINES = 3000;

export const initialSession: SessionState = {
  status: "connecting",
  agent: null,
  run: null,
  log: [],
  tabs: [],
  currentTab: null,
  frame: null,
  stream: "waiting",
  streamMessage: null,
  letter: null,
  gaps: [],
  results: {},
};

type Action =
  | { type: "status"; status: SessionStatus }
  | { type: "message"; message: AgentMessage }
  | { type: "frame"; frame: Frame };

function fromRun(state: SessionState, run: RunSnapshot | null): SessionState {
  return { ...state, run, letter: run?.letter ?? null, gaps: run?.gaps ?? [] };
}

export function sessionReducer(state: SessionState, action: Action): SessionState {
  if (action.type === "status") return { ...state, status: action.status };
  if (action.type === "frame") return { ...state, frame: action.frame, stream: "live" };
  const message = action.message;
  switch (message.type) {
    case "hello":
      return { ...fromRun({ ...state, status: "open", agent: message.device, results: {} }, message.run), log: message.log.slice(-LOG_LINES) };
    case "run":
      return fromRun(state, message.run);
    case "log":
      return { ...state, log: [...state.log, ...message.lines].slice(-LOG_LINES) };
    case "tabs":
      return { ...state, tabs: message.tabs, currentTab: message.current };
    case "letter":
      return { ...state, letter: message.text };
    case "gaps":
      return { ...state, gaps: message.items, results: {} };
    case "fact_result":
      return {
        ...state,
        gaps: message.ok ? state.gaps.filter((gap) => gap.question !== message.question) : state.gaps,
        results: { ...state.results, [message.question]: { ok: message.ok, message: message.message } },
      };
    case "stream":
      return { ...state, stream: message.state, streamMessage: message.message ?? null };
    default:
      return state;
  }
}

// After an unexpected close the page reconnects; a replaced or refused session waits for you.
function retryDelay(code: number, opened: boolean): number | null {
  if (code === CLOSE_REPLACED) return null;
  if (code === CLOSE_UNAUTHORIZED) return null;
  if (code === CLOSE_RESTARTING) return 2000;
  return opened ? 3000 : 10000;
}

function closedStatus(code: number, opened: boolean): SessionStatus {
  if (code === CLOSE_REPLACED) return "replaced";
  if (code === CLOSE_UNAUTHORIZED) return "unauthorized";
  if (code === CLOSE_RESTARTING) return "restarting";
  return opened ? "closed" : "unreachable";
}

// Keyed by the address, so the device list refreshing does not reconnect the session.
export function useDeviceSession(
  url: Device["url"],
  initialView: () => View,
  open: typeof openDeviceSession = openDeviceSession,
) {
  const [state, dispatch] = useReducer(sessionReducer, initialSession);
  const session = useRef<DeviceSession | null>(null);
  const viewRef = useRef(initialView);
  // Read once, never a dependency: the production minifier inlines the default opener
  // into the parameter, so it is a new function on every render, and an effect keyed
  // on it reconnected after every message the agent sent.
  const openRef = useRef(open);
  const [attempt, reconnect] = useReducer((count: number) => count + 1, 0);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let shown: string | null = null;
    const current = openRef.current({ url }, viewRef.current(), {
      onOpen: () => undefined,
      onMessage: (message) => dispatch({ type: "message", message }),
      onFrame: (header: FrameHeader, jpeg: Blob) => {
        const url = URL.createObjectURL(jpeg);
        const previous = shown;
        shown = url;
        dispatch({ type: "frame", frame: { url, width: header.width, height: header.height, target: header.target } });
        if (previous) setTimeout(() => URL.revokeObjectURL(previous), 1000);
      },
      onClose: (code, opened) => {
        dispatch({ type: "status", status: closedStatus(code, opened) });
        const delay = retryDelay(code, opened);
        if (delay !== null) timer = setTimeout(reconnect, delay);
      },
    });
    session.current = current;
    return () => {
      if (timer) clearTimeout(timer);
      current.close();
      session.current = null;
      if (shown) URL.revokeObjectURL(shown);
    };
  }, [url, attempt]);

  const send = useCallback((message: object) => session.current?.send(message), []);
  const retry = useCallback(() => {
    dispatch({ type: "status", status: "connecting" });
    reconnect();
  }, []);
  return { state, send, retry };
}
