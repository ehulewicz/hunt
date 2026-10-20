import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { defaultDevice, HuntDevices } from "@/components/hunt-devices";
import { HuntLive } from "@/components/hunt-live";
import { HuntRun } from "@/components/hunt-run";
import { initialSession, sessionReducer, useDeviceSession } from "@/components/use-device-session";
import type { Device } from "@/lib/api";
import { CLOSE_REPLACED, type Gap, type RunSnapshot, type SessionHandlers } from "@/lib/device";

const NOW = Date.parse("2026-10-19T12:00:00Z");

function device(overrides: Partial<Device> = {}): Device {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    name: "DESKTOP-HOME",
    url: "https://desktop-home.tail1234.ts.net",
    commit: "4c1946d0a1b2c3d4e5f60718293a4b5c6d7e8f90",
    local_changes: false,
    ready: true,
    problem: null,
    active_run_id: null,
    run: null,
    last_seen_at: "2026-10-19T11:59:50Z",
    online: true,
    ...overrides,
  };
}

const RUN: RunSnapshot = {
  key: "k", origin: "page", reference: "12", phase: "running", note: "up to date at 4c1946d",
  run_id: "r1", company: "Acme", title: "Software Engineer Intern", remote: true, outcome: null,
  letter: null, gaps: [], tab: "T1",
};

test("the device list says who is up, ready, changed, and busy", () => {
  const onSelect = vi.fn();
  render(
    <HuntDevices
      devices={[
        device(),
        device({ id: "b", name: "LAPTOP", local_changes: true, ready: false, problem: "LaTeX: pdflatex missing", run: { company_name: "Acme", title: "SWE Intern" }, active_run_id: "r1" }),
        device({ id: "c", name: "APARTMENT", online: false, last_seen_at: "2026-10-19T09:00:00Z" }),
      ]}
      now={NOW}
      onSelect={onSelect}
      selectedId="b"
    />,
  );
  const rows = screen.getAllByRole("button").map((button) => button.closest(".device-row") as HTMLElement);
  expect(within(rows[0]).getByText("Ready")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Running local changes at 4c1946d")).toBeInTheDocument();
  expect(within(rows[1]).getByText("LaTeX: pdflatex missing")).toBeInTheDocument();
  expect(within(rows[1]).getByText(/Busy: Acme — SWE Intern/)).toBeInTheDocument();
  expect(within(rows[1]).getByRole("button", { name: /LAPTOP/ })).toHaveAttribute("aria-pressed", "true");
  expect(within(rows[2]).getByRole("button", { name: /APARTMENT/ })).toBeDisabled();
  expect(within(rows[2]).getByText("Last seen 3 hours ago")).toBeInTheDocument();
  fireEvent.click(within(rows[0]).getByRole("button", { name: /DESKTOP-HOME/ }));
  expect(onSelect).toHaveBeenCalledWith(device().id);
});

test("no devices explains how to add one", () => {
  render(<HuntDevices devices={[]} now={NOW} onSelect={() => undefined} selectedId={null} />);
  expect(screen.getByText("os device install")).toBeInTheDocument();
});

function renderRun(overrides: Partial<Parameters<typeof HuntRun>[0]> = {}) {
  const handlers = { onStart: vi.fn(), onControl: vi.fn(), onLetter: vi.fn(), onFact: vi.fn() };
  render(
    <HuntRun
      connected
      deviceName="DESKTOP-HOME"
      gaps={[]}
      letter={null}
      log={[]}
      results={{}}
      run={null}
      startError={null}
      starting={false}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

test("a run starts from a reference or the top of the board", () => {
  const { onStart } = renderRun();
  fireEvent.change(screen.getByPlaceholderText("Board number, id prefix, or posting URL"), { target: { value: " 12 " } });
  fireEvent.click(screen.getByRole("checkbox", { name: "New tab" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  fireEvent.click(screen.getByRole("button", { name: "Next on board" }));
  expect(onStart.mock.calls).toEqual([["12", true], [null, true]]);
});

test("the controls drive a running run, and nothing starts over it", () => {
  const { onControl } = renderRun({ run: RUN });
  expect(screen.getByRole("button", { name: "Next on board" })).toBeDisabled();
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(screen.getByText("Software Engineer Intern")).toBeInTheDocument();
  for (const label of ["Save", "Submitted", "Skip", "Stop"]) fireEvent.click(screen.getByRole("button", { name: label }));
  expect(onControl.mock.calls.map(([action]) => action)).toEqual(["save", "done", "skip", "stop"]);
});

test("only Stop works while a run is still updating", () => {
  renderRun({ run: { ...RUN, phase: "updating", company: null } });
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
});

test("the log keeps its styles as token classes and its links", () => {
  renderRun({
    log: [[{ text: "pattern", style: ["green"] }, { text: " Name -> Ethan" }], [], [{ text: "Warning:", style: ["yellow", "bold"] }, { text: " role", link: "https://jobs.example/1" }]],
  });
  const log = screen.getByRole("list", { name: "Run log" });
  expect(within(log).getByText("pattern")).toHaveClass("log-green");
  expect(within(log).getByText("Warning:")).toHaveClass("log-yellow", "log-bold");
  expect(within(log).getByRole("link", { name: "role" })).toHaveAttribute("href", "https://jobs.example/1");
  expect(log.innerHTML).not.toMatch(/#[0-9a-f]{3,8}|rgb\(/i);
});

test("a log link to anything but the web is shown as text", () => {
  renderRun({ log: [[{ text: "click me", link: "javascript:alert(1)" }]] });
  expect(screen.queryByRole("link", { name: "click me" })).not.toBeInTheDocument();
  expect(screen.getByText("click me")).toBeInTheDocument();
});

test("the cover letter is edited here and sent back", () => {
  const { onLetter } = renderRun({ run: RUN, letter: "Dear Acme,\n[hook]\n" });
  const box = screen.getByRole("textbox", { name: /Cover letter/ });
  expect(box).toHaveValue("Dear Acme,\n[hook]\n");
  fireEvent.change(box, { target: { value: "Dear Acme,\nI build things.\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Use this letter" }));
  expect(onLetter).toHaveBeenCalledWith("Dear Acme,\nI build things.\n");
});

const GAPS: Gap[] = [
  { question: "Date of birth", cause: "fact_gap", required: true, note: "no fact identity.birth_date yet", fact_key: "identity.birth_date", missing_fact: null, label: "Date of birth", value_type: "date", settable: true },
  { question: "Clearance?", cause: "fact_gap", required: false, note: "needs a fact: security clearance status", fact_key: null, missing_fact: "security clearance status", label: null, value_type: null, settable: true },
  { question: "Pronouns", cause: "pattern_gap", required: false, note: "no pattern answers it yet", fact_key: null, missing_fact: null, label: null, value_type: null, settable: false },
];

test("a fact gap is answered inline", () => {
  const { onFact } = renderRun({ run: RUN, gaps: GAPS, results: { "Clearance?": { ok: false, message: "a fact needs a label and a type" } } });
  const gaps = screen.getByRole("region", { name: "Left for you" });
  const birth = within(gaps).getByRole("form", { name: "Fact for Date of birth" });
  fireEvent.change(within(birth).getByLabelText("Date of birth"), { target: { value: "2004-05-06" } });
  fireEvent.click(within(birth).getByRole("button", { name: "Set and fill" }));
  expect(onFact).toHaveBeenCalledWith({ question: "Date of birth", key: "identity.birth_date", label: "Date of birth", value_type: "date", value: "2004-05-06" });

  const clearance = within(gaps).getByRole("form", { name: "Fact for Clearance?" });
  expect(within(clearance).getByLabelText("Fact key")).toBeInTheDocument();
  expect(within(clearance).getByRole("alert")).toHaveTextContent("a fact needs a label and a type");
  expect(within(gaps).queryByRole("form", { name: "Fact for Pronouns" })).not.toBeInTheDocument();
  expect(within(gaps).getByText(/no pattern answers it yet/)).toBeInTheDocument();
});

test("unsent fact answers survive switching application panels", () => {
  renderRun({ run: RUN, workspace: true, letter: "Dear Acme", gaps: GAPS });
  fireEvent.change(screen.getByLabelText("Date of birth"), { target: { value: "2004-05-06" } });
  fireEvent.click(screen.getByRole("button", { name: "Cover letter" }));
  fireEvent.click(screen.getByRole("button", { name: /To finish/ }));
  expect(screen.getByLabelText("Date of birth")).toHaveValue("2004-05-06");
});

test("a new run cannot inherit edits to an identical cover letter from an earlier run", () => {
  const props = {
    connected: true, deviceName: "DESKTOP-HOME", gaps: [], letter: "Dear Acme", log: [],
    results: {}, run: RUN, onControl: vi.fn(), onFact: vi.fn(), onLetter: vi.fn(),
  };
  const { rerender } = render(<HuntRun {...props} />);
  fireEvent.change(screen.getByRole("textbox", { name: /Cover letter/ }), { target: { value: "My old application" } });
  rerender(<HuntRun {...props} run={{ ...RUN, key: "another-run" }} />);
  expect(screen.getByRole("textbox", { name: /Cover letter/ })).toHaveValue("Dear Acme");
});

test("disconnected application controls and fact forms cannot send changes", () => {
  const { onControl, onFact } = renderRun({ connected: false, run: RUN, gaps: GAPS });
  for (const name of ["Save", "Submitted", "Skip", "Stop"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toBeDisabled();
    fireEvent.click(button);
  }
  const birth = screen.getByLabelText("Date of birth");
  expect(birth).toBeDisabled();
  expect(onControl).not.toHaveBeenCalled();
  expect(onFact).not.toHaveBeenCalled();
});

function renderLive(overrides: Partial<Parameters<typeof HuntLive>[0]> = {}) {
  const send = vi.fn();
  render(
    <HuntLive
      currentTab="T2"
      deviceName="DESKTOP-HOME"
      frame={{ url: "blob:frame", width: 1280, height: 720, target: "T2" }}
      onRetry={() => undefined}
      send={send}
      status="open"
      stream="live"
      streamMessage={null}
      tabs={[{ id: "T1", title: "Board", url: "https://jobs.example/" }, { id: "T2", title: "Apply", url: "https://jobs.example/apply" }]}
      {...overrides}
    />,
  );
  return send;
}

test("the live view shows the frame, switches tabs, and sends keys and paste", () => {
  const send = renderLive();
  const surface = screen.getByRole("application", { name: /Chrome on DESKTOP-HOME: Apply/ });
  expect(surface.querySelector("img")).toHaveAttribute("src", "blob:frame");
  expect(screen.getByRole("button", { name: "Apply" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Board" }));
  fireEvent.keyDown(surface, { key: "a", code: "KeyA" });
  fireEvent.keyDown(surface, { key: "v", code: "KeyV", ctrlKey: true });
  fireEvent.paste(surface, { clipboardData: { getData: () => "pasted words" } });
  fireEvent.mouseDown(surface.querySelector("img") as Element, { clientX: 0, clientY: 0, button: 0, buttons: 1 });
  const types = send.mock.calls.map(([message]) => `${message.type}:${message.event ?? message.id ?? message.text ?? ""}`);
  expect(types).toEqual(["tab:T1", "key:down", "text:pasted words", "pointer:down"]);
});

test("an unreachable device asks about the tailnet", () => {
  renderLive({ status: "unreachable", frame: null });
  expect(screen.getByRole("status")).toHaveTextContent("Can't reach DESKTOP-HOME. Is this browser on your tailnet?");
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});

test("the session state follows what the agent says", () => {
  let state = sessionReducer(initialSession, { type: "message", message: { type: "hello", device: { id: "d", name: "DESKTOP-HOME", commit: "c", local_changes: false, ready: true, problem: null }, run: { ...RUN, gaps: GAPS }, log: [[{ text: "earlier" }]] } });
  expect(state.status).toBe("open");
  expect(state.gaps).toHaveLength(3);
  state = sessionReducer(state, { type: "message", message: { type: "fact_result", question: "Date of birth", ok: true, message: "May 6, 2004" } });
  expect(state.gaps.map((gap) => gap.question)).toEqual(["Clearance?", "Pronouns"]);
  state = sessionReducer(state, { type: "message", message: { type: "log", lines: [[{ text: "later" }]] } });
  expect(state.log.map((line) => line[0].text)).toEqual(["earlier", "later"]);
  state = sessionReducer(state, { type: "message", message: { type: "run", run: { ...RUN, phase: "ended", outcome: "submitted" } } });
  expect(state.run?.outcome).toBe("submitted");
  expect(state.gaps).toEqual([]);
});

test("a replaced session waits for you instead of fighting the other window", () => {
  vi.useFakeTimers();
  const opened: SessionHandlers[] = [];
  const open = vi.fn((_device: unknown, _view: unknown, handlers: SessionHandlers) => {
    opened.push(handlers);
    return { send: vi.fn(), close: vi.fn() };
  });
  const { result } = renderHook(() => useDeviceSession(device().url, () => ({ width: 800, height: 500, dpr: 1 }), open as never));
  act(() => opened[0].onClose(1006, true));
  expect(result.current.state.status).toBe("closed");
  act(() => { vi.advanceTimersByTime(3000); });
  expect(open).toHaveBeenCalledTimes(2);
  act(() => opened[1].onClose(CLOSE_REPLACED, true));
  expect(result.current.state.status).toBe("replaced");
  act(() => { vi.advanceTimersByTime(60000); });
  expect(open).toHaveBeenCalledTimes(2);
  vi.useRealTimers();
});

test("a session opens once, however its opener is passed", () => {
  // The production minifier inlines the default opener into the parameter, so the
  // hook gets a new function on every render; each render must not reconnect.
  const opened: SessionHandlers[] = [];
  const spy = vi.fn((_device: unknown, _view: unknown, handlers: SessionHandlers) => {
    opened.push(handlers);
    return { send: vi.fn(), close: vi.fn() };
  });
  const { result } = renderHook(() => useDeviceSession(
    device().url,
    () => ({ width: 800, height: 500, dpr: 1 }),
    ((...args: Parameters<typeof spy>) => spy(...args)) as never,
  ));
  act(() => opened[0].onMessage({ type: "hello", device: null, run: null, log: [] } as never));
  act(() => opened[0].onMessage({ type: "log", lines: [[{ text: "next" }]] } as never));
  expect(result.current.state.status).toBe("open");
  expect(spy).toHaveBeenCalledTimes(1);
});

test("a session starts on a ready online device", () => {
  expect(defaultDevice([device({ id: "a", online: false }), device({ id: "b", ready: false, problem: "x" }), device({ id: "c" })])).toBe("c");
  expect(defaultDevice([device({ id: "b", ready: false, problem: "x" })])).toBe("b");
  expect(defaultDevice([device({ id: "a", online: false })])).toBeNull();
});
