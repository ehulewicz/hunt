import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test, type WebSocketRoute } from "@playwright/test";

const DEVICE_URL = "https://desktop-home.tail1234.ts.net";
const ACCOUNT = {
  user_id: "c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f", email: "eahtree@gmail.com",
  display_name: "Eah Tree", avatar_url: null,
};
const RUN = {
  key: "k", origin: "page", reference: null, phase: "running", note: "Ready for your review",
  run_id: "r1", company: "Acme", title: "Software Engineer Intern", remote: true, outcome: null,
  letter: null as string | null, gaps: [] as object[], fixes: [] as object[], tab: "T2",
};
const APPLICATION = {
  id: "a1", posting_id: "p1", company_name: "Acme", title: "Software Engineer Intern",
  location: "New York, NY", application_url: "https://jobs.example/apply", status: "applying",
  applied_at: null, next_action: null, next_action_at: null, notes: null,
  unanswered_required_questions: ["Date of birth"], created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:00:00Z",
};
const FACTS = [
  { key: "contact.email", label: "Email", value: "alex.example@example.com", value_type: "text", verified_at: "2026-10-20T12:00:00Z" },
  { key: "work_auth.us_citizen", label: "US citizen", value: true, value_type: "boolean", verified_at: "2026-10-20T12:00:00Z" },
  { key: "availability.start_date", label: "Available start date", value: "2027-05-17", value_type: "date", verified_at: null },
  { key: "contact.phone_extension", label: "Phone extension", value: "", value_type: "text", verified_at: "2026-10-20T12:00:00Z" },
  { key: "education.certifications", label: "Certifications", value: [], value_type: "list", verified_at: null },
];
const CHANGE = {
  id: "c1", subject: "pattern", subject_id: "pat3", action: "update", before: { status: "active" }, after: { status: "retired" },
  origin: "repair", run_id: "r1", question_key: null, failure_issue_id: "i1", status: "failed", sampled: 7, failed: 2,
  created_at: "2026-10-20T12:00:00Z", verified_at: "2026-10-20T12:01:00Z",
};
const FIX = { key: "fix1", question: "Portfolio upload", cause: "widget", state: "testing", attempt: 2, note: "Checking the upload control" };
const GAP = { question: "Date of birth", cause: "fact_gap", required: true, note: "Add your date of birth to finish this field.", fact_key: "identity.birth_date", missing_fact: null, label: "Date of birth", value_type: "date", settable: true };
let jpeg: Buffer;

test.beforeAll(async ({ browser }) => {
  const remote = await browser.newPage({ viewport: { width: 960, height: 820 } });
  await remote.setContent(`<!doctype html><html lang="en"><body style="margin:0;background:#fff;color:#222;font:15px/1.6 Arial,sans-serif"><header style="padding:22px 55px;border-bottom:1px solid #eee;font-weight:700;font-size:20px">acme<span style="float:right;font-size:12px;font-weight:400;color:#666">Careers</span></header><main style="max-width:680px;margin:48px auto"><div style="font-size:12px;color:#737373">Engineering / New York, NY</div><h1 style="font-size:30px;line-height:1.2;margin:12px 0 6px">Software Engineer Intern</h1><p style="color:#737373;margin:0 0 30px">Summer 2027 · New York · Internship</p><div style="border-bottom:1px solid #ddd;padding-bottom:15px;margin-bottom:30px;font-size:13px"><span style="color:#737373;margin-right:28px">Overview</span><strong>Application</strong></div><h2 style="font-size:20px;margin-bottom:4px">Let's get to know you</h2><p style="color:#737373;font-size:13px;margin-top:0">Fields marked with * are required.</p><label style="display:block;margin:24px 0 7px">Full name *</label><div style="border:1px solid #ddd;border-radius:5px;padding:11px 14px">Alex Example</div><label style="display:block;margin:22px 0 7px">Email address *</label><div style="border:1px solid #ddd;border-radius:5px;padding:11px 14px">alex.example@example.com</div><label style="display:block;margin:22px 0 7px">Date of birth *</label><div style="border:1px solid #e9a257;border-radius:5px;padding:11px 14px;color:#888">MM / DD / YYYY</div><label style="display:block;margin:22px 0 7px">Resume *</label><div style="border:1px solid #ddd;border-radius:5px;padding:11px 14px;font-size:13px">Alex_Example.pdf <span style="float:right;color:#647b69">Uploaded</span></div><button style="margin-top:30px;background:#262626;border:0;border-radius:5px;color:white;padding:12px 24px">Submit application</button></main></body></html>`);
  jpeg = await remote.screenshot({ type: "jpeg", quality: 90 });
  await remote.close();
});

function frame(target: string): Buffer {
  const header = Buffer.from(JSON.stringify({ type: "frame", target, width: 960, height: 820 }));
  const size = Buffer.alloc(4);
  size.writeUInt32BE(header.length);
  return Buffer.concat([size, header, jpeg]);
}

async function setup(page: Page, busy = false) {
  let activeRun: typeof RUN | null = busy ? { ...RUN } : null;
  let socket: WebSocketRoute | null = null;
  let sockets = 0;
  const fromPage: Record<string, unknown>[] = [];
  const runs: unknown[] = [];
  const writes: { url: string; body: unknown }[] = [];
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  await page.addInitScript(({ session }) => localStorage.setItem("sb-test-auth-token", JSON.stringify(session)), {
    session: {
      access_token: "approved", refresh_token: "refresh", expires_in: 3600, expires_at: expiresAt,
      token_type: "bearer", user: {
        id: ACCOUNT.user_id, aud: "authenticated", role: "authenticated", email: ACCOUNT.email,
        app_metadata: { provider: "google" }, user_metadata: { full_name: "Eah Tree" },
        identities: [], created_at: "2026-10-20T12:00:00Z",
      },
    },
  });
  await page.route("https://api.test/v1/**", (route) => {
    const url = route.request().url();
    if (["PATCH", "PUT", "POST", "DELETE"].includes(route.request().method()) && !url.endsWith("/bootstrap")) {
      writes.push({ url, body: route.request().postDataJSON() });
      return route.fulfill({ json: {} });
    }
    if (url.endsWith("/jobs/devices")) return route.fulfill({ json: [{
      id: "d1", name: "DESKTOP-HOME", url: DEVICE_URL, commit: "4c1946d0a1b2c3d4e5f60718293a4b5c6d7e8f90",
      local_changes: true, ready: true, problem: null, active_run_id: activeRun ? "r1" : null,
      run: activeRun ? { company_name: "Acme", title: "Software Engineer Intern" } : null,
      last_seen_at: "2026-10-20T12:00:00Z", online: true,
    }] });
    if (url.endsWith("/jobs/applications")) return route.fulfill({ json: [
      APPLICATION,
      { ...APPLICATION, id: "a2", posting_id: "p2", company_name: "Figma", title: "Product Engineer Intern", unanswered_required_questions: [] },
      { ...APPLICATION, id: "a3", posting_id: "p3", company_name: "Ramp", title: "Software Engineer, Intern", unanswered_required_questions: ["Why Ramp?", "Start date"] },
      { ...APPLICATION, id: "a4", posting_id: "p4", company_name: "Notion", title: "Software Engineer Intern", status: "interview", applied_at: "2026-10-10T12:00:00Z" },
      { ...APPLICATION, id: "a5", posting_id: "p5", company_name: "Linear", title: "Software Engineer Intern", status: "applied", applied_at: "2026-10-15T12:00:00Z" },
    ] });
    if (url.endsWith("/jobs/facts")) return route.fulfill({ json: FACTS });
    if (url.endsWith("/jobs/questions")) return route.fulfill({ json: [
      { id: "q2", sample_label: "Your email?", form: "short", disposition: "pattern", pattern_id: "pat2" },
      { id: "q3", sample_label: "Anything else?", form: "long", disposition: "long_form", pattern_id: null },
    ] });
    if (url.endsWith("/jobs/prose")) return route.fulfill({ json: [
      { id: "pr2", question_label: "Career goals", company_name: null, text: "Build useful tools for people.\nKeep learning from their feedback.", approved: true },
    ] });
    if (url.includes("/jobs/patterns?")) return route.fulfill({ json: [
      { id: "pat2", question_id: "q2", question_label: "Your email?", expression: null, fact_key: "contact.email", formatter: null, status: "active" },
      { id: "pat3", question_id: null, question_label: null, expression: "email address", fact_key: "contact.email", formatter: null, status: "retired" },
    ] });
    if (url.includes("/jobs/changes?")) return route.fulfill({ json: url.includes("subject=code") ? [{ ...CHANGE, subject: "code", subject_id: "abc1234", after: { commit: "abc1234", summary: "Read portfolio uploads", cause: "widget", question: "Portfolio upload", attempts: 2, files: ["upload.py"], probe: { ok: true }, pushed: true, diff: "+read_upload()" } }] : [CHANGE] });
    if (url.includes("/jobs/listings?")) return route.fulfill({ json: { total: 284, items: [], limit: 1, offset: 0 } });
    if (url.endsWith("/jobs/inbox")) return route.fulfill({ json: {
      questions: [{ question: { id: "q1", sample_label: "Will you need visa sponsorship?" }, answers: [] }],
      prose: [{ id: "pr1", question_label: "Why do you want to work at Acme?", company_name: "Acme", text: "I enjoy building tools that make complicated work feel simple.", approved: false }],
      facts: FACTS.filter((fact) => !fact.verified_at).map((fact) => ({ ...fact, reason: "unverified", question: null, missing_fact: null, issue_id: null, check: null })),
      checks: [], unsolved: [{ issue_id: "issue1", category: "wall", question: "Account verification", control_kind: null, note: "Verification could not be completed", occurrence_count: 2, last_seen_at: "2026-10-20T12:00:00Z" }],
      candidates: [{ check: null, pattern: { id: "pat1", question_label: "Are you a US citizen?", fact_key: "work_auth.us_citizen", status: "candidate" }, answers: [] }],
    } });
    return route.fulfill({ json: ACCOUNT });
  });
  await page.route(`${DEVICE_URL}/runs`, async (route) => {
    const cors = { "access-control-allow-origin": "http://localhost:3000", "access-control-allow-headers": "Authorization, Content-Type", "access-control-allow-methods": "GET, POST, OPTIONS" };
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    expect(route.request().headers().authorization).toBe("Bearer approved");
    runs.push(route.request().postDataJSON());
    activeRun = { ...RUN };
    return route.fulfill({ status: 202, headers: cors, json: { run: activeRun } });
  });
  await page.routeWebSocket("wss://desktop-home.tail1234.ts.net/session", (current) => {
    socket = current;
    sockets += 1;
    current.onMessage((raw) => {
      const message = JSON.parse(String(raw)) as Record<string, unknown>;
      fromPage.push(message);
      if (message.type === "auth") {
        current.send(JSON.stringify({ type: "hello", device: { id: "d1", name: "DESKTOP-HOME", commit: "4c1946d", local_changes: true, ready: true, problem: null }, run: activeRun, log: [[{ text: "Application opened", style: ["green"] }], [{ text: "Waiting for your review", style: ["dim"] }]] }));
        current.send(JSON.stringify({ type: "tabs", tabs: [{ id: "T1", title: "Job board", url: "https://jobs.example/" }, { id: "T2", title: "Acme application", url: "https://jobs.example/apply" }], current: "T2" }));
        current.send(frame("T2"));
      }
    });
  });
  return {
    fromPage, runs, writes, sockets: () => sockets,
    send(message: Record<string, unknown>) {
      if (activeRun && message.type === "letter") activeRun = { ...activeRun, letter: message.text as string };
      if (activeRun && message.type === "fixes") activeRun = { ...activeRun, fixes: message.items as object[] };
      if (activeRun && message.type === "gaps") activeRun = { ...activeRun, gaps: message.items as object[] };
      socket?.send(JSON.stringify(message));
    },
  };
}

async function noHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("the overview fits one desktop screen and connects only after starting an application", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const agent = await setup(page);
  await page.goto("/hunt");
  await expect(page.getByRole("group", { name: "Application totals" }).getByLabel("Applied", { exact: true })).toContainText("2");
  await expect(page.getByRole("group", { name: "Application totals" }).getByLabel("Interviews", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Applications", exact: true }).getByRole("button", { name: /Resume/ })).toHaveCount(3);
  expect(agent.sockets()).toBe(0);
  await expect(page.getByRole("region", { name: "Live view" })).toHaveCount(0);
  await noHorizontalOverflow(page);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("hunt-overview.png", { animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 720 });
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  await expect(page.getByRole("heading", { name: /^Inbox/ })).toBeInViewport();
  const attention = (await page.getByRole("region", { name: "Inbox and Library" }).boundingBox())!;
  const totals = (await page.getByRole("group", { name: "Application totals" }).boundingBox())!;
  const applications = (await page.getByRole("region", { name: "Applications", exact: true }).boundingBox())!;
  expect(attention.y).toBeCloseTo(totals.y, 0);
  expect(attention.y + attention.height).toBeCloseTo(applications.y + applications.height, 0);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "Next on board" }).click();
  await expect.poll(() => agent.runs).toEqual([{ reference: null, fresh: false }]);
  const surface = page.getByRole("application", { name: /Chrome on DESKTOP-HOME: Acme application/ });
  await expect(surface.locator("img")).toHaveAttribute("src", /^blob:/);
  expect(agent.fromPage[0]).toMatchObject({ type: "auth", token: "approved" });
  const settled = agent.sockets();
  await page.waitForTimeout(1500);
  expect(agent.sockets()).toBe(settled);
  agent.send({ type: "gaps", items: [GAP] });
  agent.send({ type: "letter", text: "Dear Acme,\n[hook]\n" });
  await expect(page.getByRole("textbox", { name: "Date of birth" })).toBeVisible();
  await expect(page).toHaveScreenshot("hunt-application.png", { animations: "disabled" });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
  await page.getByRole("button", { name: "Use 50/50 layout" }).click();
  const sizes = await page.locator(".hunt-layout").evaluate((element) => Array.from(element.children).map((child) => child.getBoundingClientRect().width));
  expect(Math.abs(sizes[0] - sizes[1])).toBeLessThan(2);

  await page.getByRole("textbox", { name: "Date of birth" }).fill("2004-05-06");
  await page.getByRole("button", { name: "Cover letter", exact: true }).click();
  await page.getByRole("textbox", { name: /Cover letter/ }).fill("Dear Acme,\nI build things.\n");
  await page.getByRole("button", { name: "Back to overview" }).click();
  await expect(page.getByRole("region", { name: "Live view" })).toHaveCount(0);
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  await page.getByRole("button", { name: "Cover letter", exact: true }).click();
  await expect(page.getByRole("textbox", { name: /Cover letter/ })).toHaveValue("Dear Acme,\nI build things.\n");
  await page.getByRole("button", { name: "Use this letter" }).click();
  await page.getByRole("button", { name: /To finish/ }).click();
  await expect(page.getByRole("textbox", { name: "Date of birth" })).toHaveValue("2004-05-06");
  await page.getByRole("button", { name: "Set and fill" }).click();
  await page.getByRole("button", { name: "Job board", exact: true }).click();
  await surface.click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("a");
  await page.mouse.wheel(0, 300);
  await page.getByRole("button", { name: "Submitted", exact: true }).click();
  await expect.poll(() => agent.fromPage.map((message) => message.type)).toEqual(expect.arrayContaining(["letter", "fact", "tab", "pointer", "key", "wheel", "control"]));
  expect(agent.fromPage.find((message) => message.type === "letter")).toMatchObject({ text: "Dear Acme,\nI build things.\n" });
  expect(agent.fromPage.find((message) => message.type === "fact")).toMatchObject({ key: "identity.birth_date", value: "2004-05-06" });
  expect(agent.fromPage.find((message) => message.type === "control")).toMatchObject({ action: "done" });
  await page.locator(".run-log-drawer > summary").click();
  await expect(page.getByRole("list", { name: "Run log" })).toContainText("Application opened");
});

test("an existing terminal application opens without starting another run", async ({ page }) => {
  const agent = await setup(page, true);
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  await expect(page.getByRole("application")).toBeVisible();
  expect(agent.runs).toEqual([]);
});

test("Chrome is asked for the pane's own view and the pane never scrolls itself", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const agent = await setup(page, true);
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  const surface = page.getByRole("application");
  await expect(surface.locator("img")).toHaveAttribute("src", /^blob:/);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
    await page.setViewportSize(viewport);
    const expected = await surface.evaluate((element) => ({
      type: "view", width: element.clientWidth, height: element.clientHeight, dpr: window.devicePixelRatio,
    }));
    await expect.poll(() => agent.fromPage.filter((message) => message.type === "view").at(-1)).toEqual(expected);
    // A 960x820 frame is taller than this pane; it is fitted, not scrolled.
    const fit = await surface.evaluate((element) => {
      const pane = element.getBoundingClientRect();
      const image = element.querySelector("img")!.getBoundingClientRect();
      return {
        scrolls: element.scrollHeight > element.clientHeight || element.scrollWidth > element.clientWidth,
        inside: image.top >= pane.top - 1 && image.bottom <= pane.bottom + 1 && image.left >= pane.left - 1 && image.right <= pane.right + 1,
      };
    });
    expect(fit).toEqual({ scrolls: false, inside: true });
  }
  expect(agent.sockets()).toBe(1);
});

test("an unreachable device explains the tailnet connection and can retry", async ({ page }) => {
  await setup(page, true);
  await page.routeWebSocket("wss://desktop-home.tail1234.ts.net/session", (socket) => socket.close({ code: 1006 }));
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  await expect(page.getByRole("status")).toContainText("Can't reach DESKTOP-HOME");
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
});

test("facts, writing and pattern checks are reviewed in place with explicit writes", async ({ page }) => {
  const agent = await setup(page);
  await page.goto("/hunt");
  await page.getByRole("button", { name: /Facts to verify/ }).click();
  await page.getByRole("button", { name: "Review Available start date" }).click();
  await page.getByRole("textbox", { name: "Value", exact: true }).fill("2027-05-24");
  await page.getByRole("button", { name: "Save verified fact" }).click();
  await expect.poll(() => agent.writes).toContainEqual({ url: "https://api.test/v1/jobs/facts/availability.start_date", body: { value: "2027-05-24", verified: true, origin: "inbox" } });
  await page.getByRole("button", { name: /Writing 1/ }).click();
  await page.getByRole("button", { name: "Review Why do you want to work at Acme?" }).click();
  await page.getByRole("textbox", { name: "Answer", exact: true }).fill("I build tools people use.");
  expect(agent.writes).toHaveLength(1);
  await page.getByRole("button", { name: "Approve for reuse" }).click();
  await expect.poll(() => agent.writes.length).toBe(2);
  await page.getByRole("button", { name: /Pattern checks 1/ }).click();
  await page.getByRole("button", { name: "Review Are you a US citizen?" }).click();
  await expect(page.getByText("true", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Accept pattern" }).click();
  await expect.poll(() => agent.writes).toContainEqual({ url: "https://api.test/v1/jobs/patterns/pat1", body: { status: "active", origin: "inbox" } });
  expect(agent.sockets()).toBe(0);
});

test("a new typed fact is saved before its question is linked", async ({ page }) => {
  const agent = await setup(page);
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Review Will you need visa sponsorship?" }).click();
  await page.getByRole("button", { name: "Add a new fact" }).click();
  await page.getByRole("textbox", { name: "Fact key", exact: true }).fill("work_auth.needs_sponsorship");
  await page.getByRole("textbox", { name: "Label", exact: true }).fill("Needs sponsorship");
  await page.getByRole("combobox", { name: "Type", exact: true }).selectOption("boolean");
  await page.getByRole("textbox", { name: "Value", exact: true }).fill("false");
  await page.getByRole("button", { name: "Save fact and link" }).click();
  await expect.poll(() => agent.writes).toEqual([
    { url: "https://api.test/v1/jobs/facts/work_auth.needs_sponsorship", body: { value: false, label: "Needs sponsorship", value_type: "boolean", verified: true, origin: "inbox" } },
    { url: "https://api.test/v1/jobs/patterns", body: { kind: "binding", question_id: "q1", fact_key: "work_auth.needs_sponsorship", status: "active", origin: "inbox" } },
  ]);
});

test("a suggested pattern can be rejected without approving it", async ({ page }) => {
  const agent = await setup(page);
  await page.goto("/hunt");
  await page.getByRole("button", { name: /Pattern checks 1/ }).click();
  await page.getByRole("button", { name: "Review Are you a US citizen?" }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await expect.poll(() => agent.writes).toEqual([{ url: "https://api.test/v1/jobs/patterns/pat1", body: { status: "rejected", origin: "inbox" } }]);
});

test("Library exposes saved records, history and code changes and works on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const agent = await setup(page);
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await expect(page.getByRole("button", { name: "View Your email?" })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search questions" }).fill("Anything");
  await expect(page.getByRole("button", { name: "View Your email?" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "View Anything else?" })).toBeVisible();
  await page.getByRole("button", { name: /Patterns 2/ }).click();
  await expect(page.getByText("Retired: contact.email")).toBeVisible();
  await noHorizontalOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("hunt-library.png", { animations: "disabled" });
  await page.getByRole("button", { name: "View email address" }).click();
  await expect(page.getByText("replay changed 2 of 7 accepted answers")).toBeVisible();
  await expect(page.getByText("alex.example@example.com", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Writing 1/ }).click();
  await page.getByRole("button", { name: "Review Career goals" }).click();
  await expect(page.getByRole("textbox", { name: "Answer" })).toHaveValue("Build useful tools for people.\nKeep learning from their feedback.");
  await expect(page.getByText("Origin highlights were not recorded for this answer.")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await noHorizontalOverflow(page);
  await page.getByRole("button", { name: /Facts 5/ }).click();
  await expect(page).toHaveScreenshot("hunt-library-mobile.png", { fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Review Phone extension" }).click();
  await expect(page.getByRole("textbox", { name: "Value", exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Save verified fact" }).click();
  await expect.poll(() => agent.writes).toEqual([{ url: "https://api.test/v1/jobs/facts/contact.phone_extension", body: { value: "", verified: true, origin: "you" } }]);
  await page.getByRole("button", { name: "Inbox", exact: true }).click();
  await page.getByRole("button", { name: /Facts to verify/ }).click();
  await page.getByRole("button", { name: "Review Certifications" }).click();
  await expect(page.getByRole("textbox", { name: "Value", exact: true })).toHaveValue("[]");
  await page.getByRole("button", { name: "Save verified fact" }).click();
  await expect.poll(() => agent.writes).toContainEqual({ url: "https://api.test/v1/jobs/facts/education.certifications", body: { value: [], verified: true, origin: "inbox" } });
  expect(agent.sockets()).toBe(0);
});

test("the dashboard and application stay usable on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.goto("/hunt");
  await expect(page.getByRole("button", { name: "Resume application at Acme" })).toBeVisible();
  await noHorizontalOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page).toHaveScreenshot("hunt-overview-mobile.png", { fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Resume application at Acme" }).click();
  await expect(page.getByRole("application").locator("img")).toHaveAttribute("src", /^blob:/);
  await noHorizontalOverflow(page);
  await expect(page).toHaveScreenshot("hunt-application-mobile.png", { fullPage: true, animations: "disabled" });
});


test("live fixes update beside unresolved input and retain the kept commit note", async ({ page }) => {
  const agent = await setup(page, true);
  await page.goto("/hunt");
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  await expect(page.getByRole("application").locator("img")).toHaveAttribute("src", /^blob:/);
  agent.send({ type: "gaps", items: [GAP] });
  agent.send({ type: "fixes", items: [FIX] });
  const fixes = page.getByRole("region", { name: "Live fixes" });
  await expect(fixes).toContainText("Attempt 2/3");
  await expect(fixes).toContainText("testing");
  await expect(page.getByRole("region", { name: "Left for you" })).toBeVisible();
  agent.send({ type: "fixes", items: [{ ...FIX, state: "kept", note: "abc1234 Read portfolio uploads" }] });
  await expect(fixes).toContainText("abc1234 Read portfolio uploads");
  await page.getByRole("button", { name: "Back to overview" }).click();
  await page.getByRole("button", { name: "Open application at Acme" }).click();
  await expect(page.getByRole("region", { name: "Live fixes" })).toContainText("abc1234 Read portfolio uploads");
});

test("Unsolved notes and recorded code fixes are readable without a device session", async ({ page }) => {
  const agent = await setup(page);
  await page.goto("/hunt");
  await page.getByRole("button", { name: /Unsolved 1/ }).click();
  await expect(page.getByText("Verification could not be completed")).toBeVisible();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: "Code", exact: true }).click();
  await expect(page.getByText("Read portfolio uploads", { exact: true })).toBeVisible();
  await page.getByText("Diff", { exact: true }).click();
  await expect(page.getByText("+read_upload()", { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(agent.sockets()).toBe(0);
});

test("writing shows one provenance-tinted answer and switches to the editor", async ({ page }) => {
  await setup(page);
  await page.route("https://api.test/v1/jobs/inbox", (route) => route.fulfill({ json: {
    questions: [], candidates: [], checks: [], facts: [], unsolved: [],
    prose: [{ id: "pr1", question_label: "Why Acme?", company_name: "Acme", approved: false, origin: "model", submitted: true,
      text: "😀 mine model previous", provenance: [{ start: 7, end: 12, source: "model" }, { start: 13, end: 21, source: "previous" }] }],
  } }));
  await page.goto("/hunt");
  await page.getByRole("button", { name: /Writing 1/ }).click();
  await page.getByRole("button", { name: "Review Why Acme?" }).click();
  const answer = page.getByLabel("Answer origins");
  await expect(answer).toHaveText("😀 mine model previous");
  await expect(answer.locator("mark.prose-model")).toHaveText("model");
  await expect(answer.locator("mark.prose-previous")).toHaveText("previous");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Edit answer" }).click();
  await expect(answer).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Answer", exact: true })).toHaveValue("😀 mine model previous");
});
