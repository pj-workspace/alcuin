import { expect, test, type Page } from "@playwright/test";
import type { BootstrapPayload, Thread } from "../../packages/contracts/src/index";

async function showSidebar(page: Page) {
  if (await page.locator(".sidebar").evaluate((element) => element.classList.contains("sidebar-collapsed"))) {
    await page.getByRole("button", { name: "Toggle sidebar", exact: true }).click();
  }
}

test("first accepted message names its Thread without reloading conversation state", async ({ page }) => {
  let thread: Thread | undefined;
  let runAccepted = false;
  let titleUpdatedAt: string | undefined;
  let reads = 0;
  const title = "Checkout incident evidence";
  const namedThread = (source: Thread): Thread => ({
    ...source,
    title: runAccepted ? title : "New agent thread",
    title_status: runAccepted ? "ready" : "pending",
    updated_at: titleUpdatedAt ?? thread?.updated_at ?? thread?.created_at ?? source.created_at,
  });
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch();
    const bootstrap = await response.json() as BootstrapPayload;
    await route.fulfill({ response, json: { ...bootstrap, threads: bootstrap.threads.map((item) => item.id === thread?.id ? namedThread(item) : item) } });
  });
  await page.route(/\/v1\/threads\/[^/]+$/, async (route) => {
    const response = await route.fetch();
    const detail = await response.json() as { thread: Thread };
    await route.fulfill({ response, json: detail.thread?.id === thread?.id ? { ...detail, thread: namedThread(detail.thread) } : detail });
  });
  await page.route("**/v1/threads", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    thread = await response.json() as Thread;
    expect(route.request().postDataJSON().title).toBeUndefined();
    await route.fulfill({ response, json: namedThread(thread) });
  });
  await page.route("**/v1/threads/*/runs", async (route) => {
    const response = await route.fetch();
    runAccepted = true;
    titleUpdatedAt = new Date(Math.max(Date.now(), Date.parse(thread?.updated_at ?? thread!.created_at) + 1)).toISOString();
    await route.fulfill({ response });
  });
  await page.route(/\/v1\/threads\/[^/]+\/title(?:\/ensure)?$/, async (route) => {
    reads += 1;
    await route.fulfill({ json: namedThread(thread!) });
  });
  await page.goto("/studio?thread=new");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await composer.fill("Find the active incident");
  await composer.press("Enter");
  await expect(page.locator(".conversation-pane")).toContainText("I reviewed the current record for INC-104");
  await expect(page.locator(".thread-meta")).toContainText(title);
  await showSidebar(page);
  const titleNode = page.locator(".thread-title").filter({ hasText: title });
  await expect(titleNode).toHaveAttribute("title", title);
  await expect(titleNode).toHaveAttribute("aria-label", title);
  await expect(titleNode).not.toHaveAttribute("data-arrived", "true");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(titleNode).not.toHaveAttribute("data-arrived", "true");
  expect(reads).toBeGreaterThan(0);
});

async function legacyTitles(page: Page, delayFirst = false) {
  const threads: Thread[] = [];
  const ensured: string[] = [];
  let finish = false;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let delivered = false;
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch();
    const bootstrap = await response.json() as BootstrapPayload;
    if (!threads.length) for (let index = 0; index < 3; index += 1) threads.push({
      id: `thr_title_${index}`, workspace_id: bootstrap.workspace.id,
      agent_id: bootstrap.agents[0].id, agent_version_id: bootstrap.agents[0].current_version_id,
      title: index === 2 ? "My exact manual title" : "Working session",
      title_status: index === 2 ? "ready" : "pending", context: {}, created_at: "2026-09-12T00:00:00Z",
    });
    await route.fulfill({ response, json: { ...bootstrap, threads, runs: [] } });
  });
  await page.route(/\/v1\/threads\/thr_title_\d(?:\/title(?:\/ensure)?)?$/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const thread = threads.find((item) => path.includes(item.id))!;
    if (!path.includes("/title")) return route.fulfill({ json: { thread, messages: [], runs: [] } });
    if (path.endsWith("/ensure")) {
      ensured.push(thread.id);
      if (delayFirst && thread.id === "thr_title_0") await gate;
    }
    const ready = finish || (delayFirst && thread.id === "thr_title_1");
    await route.fulfill({ json: { ...thread, title: ready ? `清晰的会话标题 ${thread.id.at(-1)}` : thread.title, title_status: ready ? "ready" : "generating", updated_at: "2026-09-12T00:01:00Z" } }).catch(() => undefined);
    if (delayFirst && thread.id === "thr_title_0") delivered = true;
  });
  return { ensured, finish: () => { finish = true; }, release, delivered: () => delivered };
}

test("selected legacy placeholders name quietly, localize, and respect reduced motion", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("alcuin-locale", "zh"));
  await page.emulateMedia({ reducedMotion: "reduce" });
  const fixture = await legacyTitles(page);
  await page.goto("/studio?thread=thr_title_0");
  if (await page.locator(".sidebar").evaluate((element) => element.classList.contains("sidebar-collapsed"))) await page.getByRole("button", { name: "切换侧栏", exact: true }).click();
  const naming = page.locator('.thread-title[data-title-status="generating"]');
  await expect(naming).toHaveText("正在命名会话…");
  expect(await naming.evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await expect(page.locator('.thread-title[data-title-status="pending"]')).toHaveText("新会话");
  await expect(page.locator(".thread-title").filter({ hasText: "My exact manual title" })).toBeVisible();
  fixture.finish();
  await expect(page.locator(".thread-title").filter({ hasText: "清晰的会话标题 0" })).toBeVisible();
  expect(fixture.ensured).toEqual(["thr_title_0"]);
});

test("naming shimmers, reveals the completed title once, and cleans up its animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const fixture = await legacyTitles(page);
  await page.goto("/studio?thread=thr_title_0");
  await showSidebar(page);
  const naming = page.locator('.thread-title[data-title-status="generating"]');
  await expect(naming).toBeVisible();
  await expect(naming).toHaveCSS("animation-name", "thread-title-shimmer");
  // Record from animationstart to retain evidence even on a busy runner where
  // the 850ms reveal could complete between two Playwright round trips.
  await naming.evaluate((element) => {
    element.addEventListener("animationstart", (event) => {
      const animation = event as AnimationEvent;
      if (animation.animationName === "thread-title-arrive") {
        element.setAttribute("data-test-arrival-name", getComputedStyle(element).animationName);
        element.setAttribute("data-test-arrival-duration", getComputedStyle(element).animationDuration);
      }
    });
  });
  fixture.finish();
  const title = page.locator(".thread-title").filter({ hasText: "清晰的会话标题 0" });
  await expect(title).toBeVisible();
  await expect(title).toHaveAttribute("data-test-arrival-name", "thread-title-arrive");
  await expect(title).toHaveAttribute("data-test-arrival-duration", "0.85s");
  await expect(title).not.toHaveAttribute("data-arrived", "true");
  await expect(title).toHaveCSS("animation-name", "none");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(title).toHaveCSS("animation-name", "none");
  expect(fixture.ensured).toEqual(["thr_title_0"]);
});

test("a late naming response cannot rename the next selected Thread", async ({ page }) => {
  const fixture = await legacyTitles(page, true);
  await page.goto("/studio?thread=thr_title_0");
  await expect.poll(() => fixture.ensured).toEqual(["thr_title_0"]);
  await showSidebar(page);
  await page.locator(".thread-row").filter({ has: page.locator(".thread-title") }).nth(1).click();
  await expect(page).toHaveURL(/thread=thr_title_1/);
  await showSidebar(page);
  await expect(page.locator(".thread-title").filter({ hasText: "清晰的会话标题 1" })).toBeVisible();
  fixture.finish();
  fixture.release();
  await expect.poll(() => fixture.delivered()).toBe(true);
  await expect(page.locator(".thread-title").filter({ hasText: "清晰的会话标题 0" })).toHaveCount(0);
  await expect(page.locator(".thread-row.active")).toContainText("清晰的会话标题 1");
  expect(fixture.ensured).toEqual(["thr_title_0", "thr_title_1"]);
});
