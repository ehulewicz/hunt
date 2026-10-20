import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const DEVICE_URL = "https://desktop-home.tail1234.ts.net";
// A 1x1 grey JPEG, enough for the browser to decode a real frame.
const JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/yQALCAABAAEBAREA/8wABgAQEAX/2gAIAQEAAD8A0s8g/9k=",
  "base64",
);
const ACCOUNT = {
  user_id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
  email: "eahtree@gmail.com",
  display_name: "Eah Tree",
  avatar_url: null,
};
const RUN = {
  key: "k", origin: "page", reference: null, phase: "running", note: "up to date at 4c1946d",
  run_id: "r1", company: "Acme", title: "Software Engineer Intern", remote: true, outcome: null,
  letter: null, gaps: [], tab: "T2",
};

async function signedIn(page: Page) {
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  await page.addInitScript(({ session }) => {
    localStorage.setItem("sb-test-auth-token", JSON.stringify(session));
  }, {
    session: {
      access_token: "approved",
      refresh_token: "refresh",
      expires_in: 3600,
      expires_at: expiresAt,
      token_type: "bearer",
      user: {
        id: ACCOUNT.user_id, aud: "authenticated", role: "authenticated", email: ACCOUNT.email,
        app_metadata: { provider: "google" }, user_metadata: { full_name: "Eah Tree" },
        identities: [], created_at: new Date().toISOString(),
      },
    },
  });
  await page.route("https://api.test/v1/**", (route) => {
    if (route.request().url().endsWith("/v1/jobs/devices")) {
      return route.fulfill({
        json: [{
          id: "33333333-3333-4333-8333-333333333333", name: "DESKTOP-HOME", url: DEVICE_URL,
          commit: "4c1946d0a1b2c3d4e5f60718293a4b5c6d7e8f90", local_changes: true, ready: true,
          problem: null, active_run_id: null, run: null, last_seen_at: new Date().toISOString(),
          online: true,
        }],
      });
    }
    return route.fulfill({ json: ACCOUNT });
  });
}

function frame(target: string): Buffer {
  const header = Buffer.from(JSON.stringify({ type: "frame", target, width: 1280, height: 720 }));
  const size = Buffer.alloc(4);
  size.writeUInt32BE(header.length);
  return Buffer.concat([size, header, JPEG]);
}

test("the Hunt page drives a device: run, live tab, letter, and a fact", async ({ page }) => {
  await signedIn(page);
  const fromPage: Record<string, unknown>[] = [];
  const runs: unknown[] = [];
  await page.route(`${DEVICE_URL}/runs`, async (route) => {
    const cors = {
      "access-control-allow-origin": "http://localhost:3000",
      "access-control-allow-headers": "Authorization, Content-Type",
      "access-control-allow-methods": "GET, POST, OPTIONS",
    };
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    expect(route.request().headers().authorization).toBe("Bearer approved");
    runs.push(route.request().postDataJSON());
    return route.fulfill({ status: 202, headers: cors, json: { run: { ...RUN, phase: "updating" } } });
  });
  await page.routeWebSocket(`wss://desktop-home.tail1234.ts.net/session`, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw)) as Record<string, unknown>;
      fromPage.push(message);
      if (message.type === "auth") {
        socket.send(JSON.stringify({
          type: "hello",
          device: { id: "d", name: "DESKTOP-HOME", commit: "4c1946d", local_changes: true, ready: true, problem: null },
          run: null,
          log: [[{ text: "agent started", style: ["dim"] }]],
        }));
        socket.send(JSON.stringify({
          type: "tabs",
          tabs: [{ id: "T1", title: "Board", url: "https://jobs.example/" }, { id: "T2", title: "Apply", url: "https://jobs.example/apply" }],
          current: "T2",
        }));
        socket.send(frame("T2"));
      }
    });
    (page as unknown as { huntSocket: typeof socket }).huntSocket = socket;
  });

  await page.goto("/hunt");
  await expect(page.getByRole("heading", { name: "Hunt" })).toBeVisible();
  await expect(page.getByText("Running local changes at 4c1946d")).toBeVisible();
  const surface = page.getByRole("application", { name: /Chrome on DESKTOP-HOME: Apply/ });
  await expect(surface.locator("img")).toHaveAttribute("src", /^blob:/);
  await expect(page.getByRole("list", { name: "Run log" })).toContainText("agent started");
  expect(fromPage[0]).toMatchObject({ type: "auth", token: "approved" });

  await page.getByRole("button", { name: "Next on board" }).click();
  await expect.poll(() => runs).toEqual([{ reference: null, fresh: false }]);

  const socket = (page as unknown as { huntSocket: { send: (data: string) => void } }).huntSocket;
  socket.send(JSON.stringify({ type: "run", run: RUN }));
  socket.send(JSON.stringify({ type: "letter", text: "Dear Acme,\n[hook]\n" }));
  socket.send(JSON.stringify({
    type: "gaps",
    items: [{ question: "Date of birth", cause: "fact_gap", required: true, note: "no fact identity.birth_date yet", fact_key: "identity.birth_date", missing_fact: null, label: "Date of birth", value_type: "date", settable: true }],
  }));
  await expect(page.getByText("Acme — Software Engineer Intern")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.getByRole("textbox", { name: /Cover letter/ }).fill("Dear Acme,\nI build things.\n");
  await page.getByRole("button", { name: "Use this letter" }).click();
  await page.getByRole("textbox", { name: "Date of birth" }).fill("2004-05-06");
  await page.getByRole("button", { name: "Set and fill" }).click();
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await surface.click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("a");
  await page.getByRole("button", { name: "Submitted" }).click();

  await expect.poll(() => fromPage.map((message) => message.type)).toEqual(
    expect.arrayContaining(["letter", "fact", "tab", "pointer", "key", "control"]),
  );
  expect(fromPage.find((message) => message.type === "letter")).toMatchObject({ text: "Dear Acme,\nI build things.\n" });
  expect(fromPage.find((message) => message.type === "fact")).toMatchObject({ key: "identity.birth_date", value: "2004-05-06" });
  expect(fromPage.find((message) => message.type === "control")).toMatchObject({ action: "done" });
});

test("a device this browser cannot reach says so", async ({ page }) => {
  await signedIn(page);
  await page.routeWebSocket(`wss://desktop-home.tail1234.ts.net/session`, (socket) => socket.close({ code: 1006 }));
  await page.goto("/hunt");
  await expect(page.getByRole("status")).toContainText(/DESKTOP-HOME/);
});
