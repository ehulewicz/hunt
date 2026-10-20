import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { HuntLive } from "@/components/hunt-live";

const callbacks: ResizeObserverCallback[] = [];

beforeEach(() => {
  callbacks.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal("devicePixelRatio", 1.25);
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) { callbacks.push(callback); }
    observe() {}
    disconnect() {}
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function sizeSurface(width: number, height: number) {
  const surface = screen.getByRole("application");
  Object.defineProperties(surface, {
    clientWidth: { configurable: true, value: width },
    clientHeight: { configurable: true, value: height },
  });
}

const props = {
  deviceName: "EXAMPLEPC", status: "open" as const, tabs: [], currentTab: null,
  frame: { url: "blob:frame", width: 1065, height: 1751, target: "T1" },
  stream: "live" as const, streamMessage: null, onRetry: () => undefined,
};

test("a short pane requests a full-width capture from an older agent", () => {
  const send = vi.fn();
  render(<HuntLive {...props} send={send} />);
  sizeSurface(1000, 600);
  act(() => {
    callbacks[0]([], {} as ResizeObserver);
    vi.advanceTimersByTime(300);
  });
  expect(send).toHaveBeenLastCalledWith({ type: "view", width: 1000, height: 1645, dpr: 1.25 });

  sizeSurface(700, 600);
  act(() => {
    callbacks.at(-1)!([], {} as ResizeObserver);
    vi.advanceTimersByTime(300);
  });
  expect(send).toHaveBeenLastCalledWith({ type: "view", width: 700, height: 1151, dpr: 1.25 });
});

test("reconnecting sends the native aspect ratio again without waiting for a pane resize", () => {
  const send = vi.fn();
  const { rerender } = render(<HuntLive {...props} send={send} status="closed" />);
  sizeSurface(1000, 600);
  act(() => { vi.advanceTimersByTime(300); });
  send.mockClear();
  rerender(<HuntLive {...props} send={send} />);
  act(() => { vi.advanceTimersByTime(300); });
  expect(send).toHaveBeenLastCalledWith({ type: "view", width: 1000, height: 1645, dpr: 1.25 });
});
