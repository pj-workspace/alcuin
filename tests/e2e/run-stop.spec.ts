import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const api = "http://localhost:8100";
const headers = { "X-Alcuin-Workspace": "ws_demo" };
type Run = { id: string; thread_id: string; status: string; events: Array<{ type: string; payload: Record<string, unknown> }> };

async function readRun(request: APIRequestContext, id: string): Promise<Run> {
  const response = await request.get(`${api}/v1/runs/${id}`, { headers });
  expect(response.ok()).toBe(true);
  return response.json();
}

function answer(run: Run) {
  return run.events.filter((event) => event.type === "message.delta").map((event) => String(event.payload.delta ?? "")).join("");
}

async function submit(page: Page, prompt: string, locale: "en" | "zh" = "en"): Promise<Run> {
  const created = page.waitForResponse((response) => /\/v1\/threads\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
  const input = page.getByRole("textbox", { name: locale === "zh" ? "消息输入" : "Message input", exact: true });
  await input.fill(prompt);
  await input.press("Enter");
  const response = await created;
  expect(response.status()).toBe(202);
  return response.json();
}

async function setup(page: Page, locale: "en" | "zh") {
  await page.addInitScript((language) => {
    localStorage.setItem("alcuin-locale", language);
    localStorage.setItem("alcuin-theme", "dark");
  }, locale);
  await page.goto("/studio?thread=new");
}

for (const locale of ["en", "zh"] as const) {
  test(`real stop preserves partial output across refresh and the next turn (${locale}, dark)`, async ({ page, request }, testInfo) => {
    test.setTimeout(60_000);
    await setup(page, locale);
    const gateId = randomUUID();
    const run = await submit(page, `Search incidents and summarize [continuation-gate:${gateId}]`, locale);
    let partial = "";
    try {
      await expect.poll(async () => answer(await readRun(request, run.id))).toContain("I reviewed");
      const before = await readRun(request, run.id);
      partial = answer(before);
      expect(before.status).toBe("running");
      expect(before.events.filter((event) => event.type === "tool.completed" && event.payload.tool === "ops.search_incidents")).toHaveLength(1);
      await expect(page.locator(".assistant-output")).toContainText(partial);
      const cancelled = page.waitForResponse((response) => response.url().endsWith(`/v1/runs/${run.id}/cancel`) && response.request().method() === "POST");
      await page.getByRole("button", { name: locale === "zh" ? "停止生成" : "Stop generation", exact: true }).click();
      expect((await cancelled).ok()).toBe(true);
      await expect.poll(async () => (await readRun(request, run.id)).status).toBe("cancelled");
      const stopped = await readRun(request, run.id);
      expect(answer(stopped)).toBe(partial);
      expect(stopped.events.filter((event) => event.type === "run.cancel_requested")).toHaveLength(1);
      expect(stopped.events.filter((event) => event.type === "run.cancelled")).toHaveLength(1);
      expect(stopped.events.some((event) => ["run.completed", "run.failed"].includes(event.type))).toBe(false);
      await expect(page.locator(".run-stop-receipt")).toContainText(locale === "zh" ? "已停止，保留已生成内容。" : "Generation stopped. Existing output was kept.");
      await page.reload();
      await expect(page.locator(".assistant-output")).toHaveCount(1);
      await expect(page.locator(".assistant-output")).toContainText(partial);
      await expect(page.locator(".run-stop-receipt")).toHaveCount(1);
      await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expect(page.locator(".run-stop-receipt")).toHaveCSS("opacity", "1");
      await testInfo.attach(`stop-${locale}-dark`, { body: await page.screenshot({ path: testInfo.outputPath(`stop-${locale}-dark.png`) }), contentType: "image/png" });
    } finally {
      const release = await request.post(`http://127.0.0.1:9412/_test/provider-gates/${gateId}/release`);
      expect(release.ok()).toBe(true);
    }
    const next = await submit(page, "Find the current record", locale);
    expect(next.id).not.toBe(run.id);
    // The fixture sees the prior user turn's marker in conversation history.
    // Release that deterministic gate again once the next answer has begun.
    await expect.poll(async () => answer(await readRun(request, next.id))).toContain("I reviewed");
    expect((await request.post(`http://127.0.0.1:9412/_test/provider-gates/${gateId}/release`)).ok()).toBe(true);
    await expect.poll(async () => (await readRun(request, next.id)).status, { timeout: 15_000 }).toBe("completed");
    await expect(page.locator(".assistant-output")).toHaveCount(2);
    await expect(page.locator(".assistant-output").first()).toContainText(partial);
    expect(answer(await readRun(request, run.id))).toBe(partial);
    const threadResponse = await request.get(`${api}/v1/threads/${run.thread_id}`, { headers });
    const thread = await threadResponse.json() as { runs: Run[]; messages: Array<{ role: string }> };
    expect(thread.runs).toHaveLength(2);
    expect(thread.messages.filter((message) => message.role === "assistant")).toHaveLength(2);
    await page.reload();
    await expect(page.locator(".assistant-output")).toHaveCount(2);
    await expect(page.locator(".run-stop-receipt")).toHaveCount(1);
  });
}

test("stopping an approval wait closes Allow and rejects its stale action", async ({ page, request }) => {
  await setup(page, "en");
  const run = await submit(page, "Update this incident to monitoring");
  await expect(page.getByRole("button", { name: "Allow once", exact: true })).toBeVisible({ timeout: 15_000 });
  const waiting = await readRun(request, run.id);
  const approval = waiting.events.find((event) => event.type === "approval.required");
  expect(approval).toBeDefined();
  await page.getByRole("button", { name: "Stop generation", exact: true }).click();
  await expect(page.locator(".run-stop-receipt")).toContainText("Generation stopped. Existing output was kept.");
  await expect(page.getByRole("button", { name: "Allow once", exact: true })).toHaveCount(0);
  const stale = await request.post(`${api}/v1/runs/${run.id}/approvals/${approval!.payload.approval_id}`, { headers, data: { decision: "approved" } });
  expect(stale.status()).toBe(409);
  const done = await readRun(request, run.id);
  expect(done.status).toBe("cancelled");
  expect(done.events.some((event) => event.type === "tool.completed" && event.payload.tool === "ops.update_ticket")).toBe(false);
  await page.reload();
  await expect(page.getByRole("button", { name: "Allow once", exact: true })).toHaveCount(0);
  await expect(page.locator(".run-stop-receipt")).toHaveCount(1);
});

test("a failed stop request stays truthful and retries the real cancellation", async ({ page, request }) => {
  await setup(page, "en");
  const gateId = randomUUID();
  const run = await submit(page, `Search incidents [continuation-gate:${gateId}]`);
  let attempts = 0;
  try {
    await expect.poll(async () => answer(await readRun(request, run.id))).toContain("I reviewed");
    await page.route(`**/v1/runs/${run.id}/cancel`, async (route) => {
      attempts += 1;
      if (attempts === 1) await route.abort("failed");
      else await route.continue();
    });
    await page.getByRole("button", { name: "Stop generation", exact: true }).click();
    await expect(page.locator(".composer-wrap").getByRole("alert")).toContainText("Stop is not confirmed. Try again.");
    expect((await readRun(request, run.id)).status).toBe("running");
    await expect(page.locator(".run-stop-receipt")).toHaveCount(0);
    await page.getByRole("button", { name: "Retry stop", exact: true }).click();
    await expect.poll(async () => (await readRun(request, run.id)).status).toBe("cancelled");
    await expect(page.locator(".run-stop-receipt")).toContainText("Generation stopped. Existing output was kept.");
    expect(attempts).toBe(2);
    expect((await readRun(request, run.id)).events.filter((event) => event.type === "run.cancelled")).toHaveLength(1);
  } finally {
    const release = await request.post(`http://127.0.0.1:9412/_test/provider-gates/${gateId}/release`);
    expect(release.ok()).toBe(true);
  }
});

test("stopping a question closes its answer controls and pending trace across reload", async ({ page, request }) => {
  await setup(page, "en");
  const run = await submit(page, "Ask my audience before preparing a report");
  await expect(page.getByRole("region", { name: "A question for you", exact: true })).toBeVisible({ timeout: 15_000 });
  const waiting = await readRun(request, run.id);
  const input = waiting.events.find((event) => event.type === "input.required");
  expect(input).toBeDefined();
  await expect(page.locator(".brainstorm-toggle").last()).toContainText("Waiting for your answer");
  await page.getByRole("button", { name: "Stop generation", exact: true }).click();
  await expect.poll(async () => (await readRun(request, run.id)).status).toBe("cancelled");
  for (const reload of [false, true]) {
    if (reload) await page.reload();
    await expect(page.getByRole("region", { name: "This question is closed", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Answer and continue", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Skip and let AI continue", exact: true })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Your answer or additional details", exact: true })).toHaveCount(0);
    const trace = page.locator(".brainstorm-toggle").last();
    await expect(trace).not.toContainText("Waiting for your answer");
    if (await trace.getAttribute("aria-expanded") !== "true") await trace.click();
    await expect(page.locator(".brainstorm-collapse").last()).not.toHaveAttribute("inert");
    await expect(page.locator(".brainstorm-collapse").last()).not.toContainText("Waiting for your answer");
    await expect(page.locator(".brainstorm-node.tool .micro-loader")).toHaveCount(0);
  }
  const stale = await request.post(`${api}/v1/runs/${run.id}/inputs/${input!.payload.input_id}`, { headers, data: { answer: "Management" } });
  expect(stale.status()).toBe(409);
  expect((await readRun(request, run.id)).events.some((event) => event.type === "input.answered")).toBe(false);
});
