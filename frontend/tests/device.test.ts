import { expect, test, vi } from "vitest";

import {
  CLOSE_REPLACED,
  deviceOrigin,
  deviceRequest,
  DeviceUnreachable,
  keyMessage,
  openDeviceSession,
  parseFrame,
  pointerMessage,
  wheelMessage,
} from "@/lib/device";

const DEVICE = { url: "https://desktop-home.tail1234.ts.net" };

test("only a device's own tailnet address gets the token", () => {
  expect(deviceOrigin(DEVICE)).toBe("https://desktop-home.tail1234.ts.net");
  for (const url of ["http://desktop.tail1.ts.net", "https://evil.example", "https://x.ts.net.evil.com", "https://user@x.ts.net"]) {
    expect(() => deviceOrigin({ url })).toThrow();
  }
});

test("a frame is a length, a JSON header, then the JPEG", () => {
  const header = new TextEncoder().encode(JSON.stringify({ type: "frame", target: "T1", width: 1280, height: 720 }));
  const buffer = new Uint8Array(4 + header.length + 3);
  new DataView(buffer.buffer).setUint32(0, header.length);
  buffer.set(header, 4);
  buffer.set([0xff, 0xd8, 0x01], 4 + header.length);
  const { header: parsed, jpeg } = parseFrame(buffer.buffer);
  expect(parsed).toEqual({ type: "frame", target: "T1", width: 1280, height: 720 });
  expect(Array.from(jpeg)).toEqual([0xff, 0xd8, 0x01]);
});

test("keys go as the DOM's own fields", () => {
  const event = { key: "A", code: "KeyA", location: 0, repeat: false, ctrlKey: false, altKey: false, metaKey: false, shiftKey: true };
  expect(keyMessage(event, "down")).toEqual({ type: "key", event: "down", key: "A", code: "KeyA", location: 0, repeat: false, ctrl: false, alt: false, meta: false, shift: true });
});

test("pointer and wheel positions are fractions of the shown frame", () => {
  const box = { left: 100, top: 50, width: 400, height: 200 };
  const click = pointerMessage({ clientX: 300, clientY: 100, button: 0, buttons: 1, detail: 2, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false }, box, "down");
  expect(click).toMatchObject({ type: "pointer", event: "down", x: 0.5, y: 0.25, clickCount: 2 });
  const wheel = wheelMessage({ clientX: 100, clientY: 250, deltaX: 0, deltaY: 3, deltaMode: 1, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false }, box);
  expect(wheel).toMatchObject({ type: "wheel", x: 0, y: 1, dy: 3, mode: 1 });
});

test("an unreachable device is told apart from one that refused", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await expect(deviceRequest(DEVICE, "/status", {}, async () => "token")).rejects.toBeInstanceOf(DeviceUnreachable);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "A run is already in progress on this device" }), { status: 409 })));
  await expect(deviceRequest(DEVICE, "/runs", { method: "POST", body: "{}" }, async () => "token")).rejects.toMatchObject({ status: 409, message: "A run is already in progress on this device" });
  vi.unstubAllGlobals();
});

class StubSocket {
  static OPEN = 1;
  static last: StubSocket;
  readyState = 0;
  binaryType = "blob";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  constructor(readonly url: string) { StubSocket.last = this; }
  send(data: string) { this.sent.push(data); }
  close() { this.onclose?.({ code: 1000 }); }
}

test("the session sends the fresh token first and hands messages on", async () => {
  const messages: unknown[] = [];
  const closes: [number, boolean][] = [];
  openDeviceSession(DEVICE, { width: 800, height: 500, dpr: 2 }, {
    onOpen: () => undefined,
    onMessage: (message) => messages.push(message),
    onFrame: () => undefined,
    onClose: (code, opened) => closes.push([code, opened]),
  }, async () => "fresh-token", StubSocket as unknown as typeof WebSocket);
  await Promise.resolve();
  const socket = StubSocket.last;
  expect(socket.url).toBe("wss://desktop-home.tail1234.ts.net/session");
  socket.readyState = 1;
  socket.onopen?.();
  expect(JSON.parse(socket.sent[0])).toEqual({ type: "auth", token: "fresh-token", view: { width: 800, height: 500, dpr: 2 } });
  socket.onmessage?.({ data: JSON.stringify({ type: "letter", text: "Dear Acme" }) });
  socket.onclose?.({ code: CLOSE_REPLACED });
  expect(messages).toEqual([{ type: "letter", text: "Dear Acme" }]);
  expect(closes).toEqual([[CLOSE_REPLACED, true]]);
});
