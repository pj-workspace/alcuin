import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import type { BootstrapPayload, ExecutionEvent, Run, Thread } from "../../packages/contracts/src/index";

const api = "http://localhost:8100";
const headers = { "X-Alcuin-Workspace": "ws_demo" };
type RunSnapshot = Run & { events: ExecutionEvent[] };

test.afterEach(async ({ page }) => {
  // Navigation may start a bootstrap fetch after its visible UI is ready.
  // Drain route handlers before Playwright closes this test's page/context.
  await page.unrouteAll({ behavior: "wait" });
});

async function readRun(request: APIRequestContext, id: string): Promise<RunSnapshot> {
  const response = await request.get(`${api}/v1/runs/${id}`, { headers });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function completedRun(request: APIRequestContext, label: string, prompt = "Find the active incident"): Promise<RunSnapshot> {
  const createdThread = await request.post(`${api}/v1/threads`, {
    headers, data: { agent_id: "agt_operations", title: `Receipt ${label}`, context: {} },
  });
  expect(createdThread.ok()).toBe(true);
  const thread = await createdThread.json() as Thread;
  const createdRun = await request.post(`${api}/v1/threads/${thread.id}/runs`, {
    headers, data: { input: `${prompt} — ${label} ${randomUUID()}`, thinking: true },
  });
  expect(createdRun.status()).toBe(202);
  const run = await createdRun.json() as Run;
  await expect.poll(async () => (await readRun(request, run.id)).status, { timeout: 20_000 }).toBe("completed");
  return readRun(request, run.id);
}

async function records(page: Page, mobile: boolean) {
  if (mobile && await page.getByRole("button", { name: "Back to records", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "Back to records", exact: true }).click();
  }
}

async function selectRecord(page: Page, run: RunSnapshot, mobile: boolean) {
  await records(page, mobile);
  await page.locator(`.activity-record[data-run-id="${run.id}"]`).click();
}

async function receipt(page: Page) {
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download receipt", exact: true }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  const path = await download.path();
  expect(path).not.toBeNull();
  // Compare user-visible Markdown text while allowing normal punctuation escaping.
  return (await readFile(path!, "utf8")).replace(/\\([\\`*_{}\[\]()<>#!|~])/g, "$1");
}

test("completed work can be found, filtered, exported, and opened in its original conversation", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const mobile = testInfo.project.name === "mobile";
  const run = await completedRun(request, "receipt history");
  expect(run.events.some((event) => event.type === "citation.created")).toBe(true);
  expect(run.events.some((event) => event.type === "tool.completed")).toBe(true);
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Runs", level: 1, exact: true })).toBeVisible();
  await records(page, mobile);
  await page.getByRole("searchbox", { name: "Search recent work" }).fill(run.input);
  await expect(page.locator(".activity-record")).toHaveCount(1);
  const filter = page.getByRole("combobox", { name: "Filter records" });
  await filter.selectOption("failed");
  await expect(page.locator(".activity-record")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No matching records", exact: true })).toBeVisible();
  await filter.selectOption("completed");
  await expect(page.locator(".activity-record")).toHaveCount(1);
  await page.getByRole("button", { name: "Refresh records", exact: true }).click();
  await selectRecord(page, run, mobile);
  const detail = page.getByRole("region", { name: "Work details", exact: true });
  await expect(detail.getByRole("heading", { level: 2, name: run.input, exact: true })).toBeVisible();
  await expect(detail).toContainText("I reviewed the current record for INC-104");
  await expect(detail.getByRole("region", { name: "Sources & evidence", exact: true })).toBeVisible();
  await detail.getByRole("button", { name: /Sources & evidence/ }).click();
  await expect(detail.locator(".citation-source-list")).toContainText("Active incident");
  await expect(detail).not.toContainText("Using the explicitly bound capability.");
  const timeline = detail.locator(".activity-timeline");
  await expect(timeline).toContainText("Work completed");
  const timelineText = await timeline.innerText();
  const milestones = ["Work started", "Tool requested", "Tool outcome recorded", "Work completed"];
  const positions = milestones.map((label) => timelineText.indexOf(label));
  expect(positions.every((position) => position >= 0)).toBe(true);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  expect(timelineText).not.toMatch(/message\.delta|reasoning\.delta|model\.request|"arguments"/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  const markdown = await receipt(page);
  expect(markdown).toContain(run.id);
  expect(markdown).toMatch(/completed/i);
  expect(markdown).toContain("ops.search_incidents");
  expect(markdown).toMatch(/succeeded/i);
  expect(markdown).toContain("Active incident");
  expect(markdown).not.toContain(run.input);
  expect(markdown).not.toContain("I reviewed the current record for INC-104");
  expect(markdown).not.toContain("Using the explicitly bound capability.");
  expect(markdown).not.toMatch(/"(?:arguments|reasoning_content|query)"\s*:/);
  await detail.evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));
  await testInfo.attach("work-receipt", {
    body: await page.screenshot({ path: testInfo.outputPath("work-receipt.png") }), contentType: "image/png",
  });

  const conversation = detail.getByRole("link", { name: "Open conversation", exact: true });
  const destination = new URL((await conversation.getAttribute("href"))!, page.url());
  expect(destination.pathname).toBe("/studio");
  expect(destination.searchParams.get("thread")).toBe(run.thread_id);
  await conversation.click();
  await expect(page).toHaveURL(new RegExp(`thread=${run.thread_id}`));
  await expect(page.locator(".conversation-pane")).toContainText(run.input);
  await expect(page.locator(".assistant-output")).toContainText("I reviewed the current record for INC-104");
});

test("a late record response cannot replace the selected work or its receipt", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const mobile = testInfo.project.name === "mobile";
  const first = await completedRun(request, "late first record", "Search the web for Alcuin");
  const second = await completedRun(request, "selected second record");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let started = false;
  let delivered = false;
  await page.route(`**/v1/runs/${first.id}`, async (route) => {
    started = true;
    await gate;
    await route.fulfill({ json: first }).catch(() => undefined);
    delivered = true;
  });
  try {
    await page.goto("/runs");
    await selectRecord(page, first, mobile);
    await expect.poll(() => started).toBe(true);
    await selectRecord(page, second, mobile);
    const detail = page.getByRole("region", { name: "Work details", exact: true });
    await expect(detail.getByRole("heading", { level: 2, name: second.input, exact: true })).toBeVisible();
    await expect(detail).toContainText("I reviewed the current record for INC-104");
    release();
    await expect.poll(() => delivered).toBe(true);
    await expect(detail.getByRole("heading", { level: 2, name: second.input, exact: true })).toBeVisible();
    await expect(detail).not.toContainText(first.input);
    await expect(detail).not.toContainText("Web evidence confirms");
    await expect(detail).toContainText("I reviewed the current record for INC-104");
    const markdown = await receipt(page);
    expect(markdown).toContain(second.id);
    expect(markdown).not.toContain(first.id);
    expect(markdown).not.toContain(first.input);
    expect(markdown).not.toContain("Web evidence confirms");
    expect(markdown).not.toContain("https://example.com/alcuin-agent-platform");
    const href = await detail.getByRole("link", { name: "Open conversation", exact: true }).getAttribute("href");
    expect(new URL(href!, page.url()).searchParams.get("thread")).toBe(second.thread_id);
  } finally {
    release();
  }
});

test("a failed detail request stays explicit and can recover without stale output", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const mobile = testInfo.project.name === "mobile";
  const run = await completedRun(request, "retry record");
  let fail = true;
  await page.route(`**/v1/runs/${run.id}`, async (route) => {
    if (fail) await route.fulfill({ status: 503, json: { detail: "Fixture temporarily unavailable" } });
    else await route.continue();
  });
  await page.goto("/runs");
  await selectRecord(page, run, mobile);
  await expect(page.getByText("Could not load this record.", { exact: true })).toBeVisible();
  await expect(page.locator(".activity-detail")).not.toContainText("I reviewed the current record for INC-104");
  fail = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  const detail = page.getByRole("region", { name: "Work details", exact: true });
  await expect(detail).toContainText("I reviewed the current record for INC-104");
  await expect(page.getByText("Could not load this record.", { exact: true })).toHaveCount(0);
  await expect(detail.getByRole("heading", { level: 2, name: run.input, exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Chinese dark receipts respect reduced motion and mobile list navigation", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const mobile = testInfo.project.name === "mobile";
  const run = await completedRun(request, "中文深色执行摘要");
  await page.addInitScript(() => {
    localStorage.setItem("alcuin-locale", "zh");
    localStorage.setItem("alcuin-theme", "dark");
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/runs");
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("heading", { name: "运行记录", level: 1, exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "搜索最近的执行", exact: true }).fill(run.input);
  await page.getByRole("combobox", { name: "筛选记录", exact: true }).selectOption("completed");
  await page.locator(`.activity-record[data-run-id="${run.id}"]`).click();
  const detail = page.getByRole("region", { name: "执行详情", exact: true });
  await expect(detail.getByRole("heading", { name: "结果", exact: true })).toBeVisible();
  await expect(detail.getByRole("link", { name: "打开原对话", exact: true })).toBeVisible();
  await expect(detail.getByRole("button", { name: "下载执行摘要", exact: true })).toBeEnabled();
  await expect(detail.getByRole("region", { name: "来源与证据", exact: true })).toBeVisible();
  await expect(detail.locator(".activity-timeline")).toContainText("执行完成");
  const content = detail.locator(".activity-detail-content");
  await expect(content).toHaveCSS("opacity", "1");
  await expect(content).toHaveCSS("transform", "none");
  await expect.poll(() => page.locator(".activity-surface").evaluate((element) =>
    element.getAnimations({ subtree: true }).filter((animation) => ["running", "pending"].includes(animation.playState)).length,
  )).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await detail.evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));
  await testInfo.attach("work-receipt-zh-dark", {
    body: await page.screenshot({ path: testInfo.outputPath("work-receipt-zh-dark.png") }), contentType: "image/png",
  });
  if (mobile) {
    await expect(page.locator(".activity-list")).toBeHidden();
    await page.getByRole("button", { name: "返回记录列表", exact: true }).click();
    await expect(detail).toBeHidden();
    await expect(page.locator(".activity-list")).toBeVisible();
    await expect(page.locator(`.activity-record[data-run-id="${run.id}"]`)).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test("an empty record history offers a real conversation entry point", async ({ page, request }) => {
  const response = await request.get(`${api}/v1/bootstrap`, { headers });
  expect(response.ok()).toBe(true);
  const bootstrap = await response.json() as BootstrapPayload;
  await page.route("**/v1/bootstrap", async (route) => {
    await route.fulfill({ json: { ...bootstrap, runs: [] } });
  });
  await page.route(/\/v1\/runs$/, (route) => route.fulfill({ json: [] }));
  await page.goto("/runs");
  await expect(page.locator(".activity-record")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Your work will appear here", exact: true })).toBeVisible();
  const start = page.locator(".activity-list").getByRole("link", { name: "Start a conversation", exact: true });
  await expect(start).toHaveAttribute("href", "/studio?thread=new");
  await page.getByRole("button", { name: "Refresh records", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your work will appear here", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await start.click();
  await expect(page).toHaveURL(/\/studio\?thread=new$/);
  await expect(page.getByRole("textbox", { name: "Message input", exact: true })).toBeVisible();
});
