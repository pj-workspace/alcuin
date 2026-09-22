import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const fixture = readFileSync(resolve(process.cwd(), "tests/fixtures/streaming-performance.cjs"), "utf8");

declare global {
  interface Window {
    __benchmarkCopied?: string;
    __orbPaintCount?: (canvas: HTMLCanvasElement) => number;
    __streamingBenchmark: {
      expected: { reasoning: string; answer: string; fragments: number };
      snapshot(): Record<string, number>;
    };
  }
}

test("presence motion runs only for active visible work and respects reduced motion", async ({ page }) => {
  // Count actual canvas paints, not CSS classes or a production-only test hook.
  await page.addInitScript(() => {
    const counts = new WeakMap<HTMLCanvasElement, number>();
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (this.canvas instanceof HTMLCanvasElement) counts.set(this.canvas, (counts.get(this.canvas) ?? 0) + 1);
      return clear.apply(this, args);
    };
    window.__orbPaintCount = (canvas) => counts.get(canvas) ?? 0;
  });
  const escapedWrites = await openBenchmark(page, 12_000);
  const trace = page.locator(".brainstorm-toggle").last();
  const canvas = trace.locator(".presence-orb-layer:not(.presence-orb-layer-out) canvas");
  const count = () => canvas.evaluate((node) => window.__orbPaintCount!(node as HTMLCanvasElement));
  await expect(page.locator(".thinking-md-content").last()).toContainText("检查固定的合成材料");
  await expect(trace).toHaveAttribute("aria-expanded", "true");
  const activePaints = await count();
  await expect.poll(count).toBeGreaterThan(activePaints + 2);
  const pulse = page.locator(".brainstorm-node.live").last();
  await trace.click();
  await expect(trace).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".brainstorm-collapse").last()).toHaveAttribute("inert", "");
  await expect(pulse).toHaveCSS("animation-play-state", "paused");
  await trace.click();
  await expect(pulse).toHaveCSS("animation-play-state", "running");
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Wait for effects/media listeners to settle, then sample a bounded window.
  await page.waitForTimeout(100);
  const reducedPaints = await count();
  await page.waitForTimeout(350);
  expect(await count()).toBe(reducedPaints);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect.poll(count).toBeGreaterThan(reducedPaints + 2);
  await completedMetrics(page);
  await expect(page.locator(".run-brainstorm.streaming")).toHaveCount(0);
  await trace.scrollIntoViewIfNeeded();
  await expect(trace.locator(".presence-orb-layer-out")).toHaveCount(0);
  await page.waitForTimeout(100);
  const finishedPaints = await count();
  await page.waitForTimeout(500);
  expect(await count()).toBe(finishedPaints);
  expect(escapedWrites).toEqual([]);
});

async function openBenchmark(page: Page, durationMs: number) {
  // The init script intercepts all writes before the application boots. Only
  // bootstrap/other read-only catalog calls can reach the real local API.
  const escapedWrites: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/v1/") && !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
      escapedWrites.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  });
  await page.addInitScript({ content: `${fixture}\ninstallStreamingBenchmark(${JSON.stringify({ durationMs, fragments: 3200, batchMs: 100 })});` });
  await page.addInitScript(() => {
    localStorage.setItem("alcuin-locale", "en");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (text: string) => { window.__benchmarkCopied = text; } },
    });
  });
  await page.goto("/studio?thread=new");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await expect(composer).toBeVisible();
  await composer.fill("Run the isolated streaming benchmark.");
  await composer.press("Enter");
  return escapedWrites;
}

async function completedMetrics(page: Page) {
  await expect.poll(() => page.evaluate(() => window.__streamingBenchmark.snapshot().finalDomMs), { timeout: 20_000 }).toBeGreaterThanOrEqual(0);
  const result = await page.evaluate(() => ({ metrics: window.__streamingBenchmark.snapshot(), expected: window.__streamingBenchmark.expected }));
  expect(result.metrics.emittedFragments).toBe(result.expected.fragments);
  expect(result.metrics.emittedReasoningChars).toBe(result.expected.reasoning.length);
  expect(result.metrics.emittedAnswerChars).toBe(result.expected.answer.length);
  expect(result.metrics.streamConnections).toBe(1);
  expect(result.metrics.blockedWrites).toBe(0);
  // Ordering/completeness assertions, deliberately no FPS or wall-clock budget.
  expect(result.metrics.firstAnswerDomMs).toBeGreaterThanOrEqual(result.metrics.firstAnswerDeltaMs);
  expect(result.metrics.finalDomMs).toBeGreaterThanOrEqual(result.metrics.finalDeltaMs);
  return result;
}

test("fragmented SSE progressively renders, preserves Markdown, and copies every final character", async ({ page }, testInfo) => {
  const escapedWrites = await openBenchmark(page, 8_000);
  const answer = page.locator(".agent-turn .markdown-content").last();
  await expect.poll(() => answer.textContent().catch(() => ""), { timeout: 15_000 }).toContain("基准正文开始");
  // A visible partial answer must arrive while the stream is still unfinished.
  expect(await page.evaluate(() => window.__streamingBenchmark.snapshot().finalDeltaMs)).toBe(-1);
  const partialLength = (await answer.textContent())!.length;
  await expect.poll(async () => (await answer.textContent())!.length).toBeGreaterThan(partialLength);

  const { metrics, expected } = await completedMetrics(page);
  await expect(answer.locator("table")).toHaveCount(10);
  await expect(answer.locator("pre code")).toHaveCount(10);
  await expect(answer.locator("ul > li")).toHaveCount(20);
  await expect(answer.locator('a[href="https://example.com/benchmark"]')).toHaveCount(10);
  await expect(answer).toContainText("BENCHMARK_COMPLETE_3200");
  await page.getByRole("button", { name: "Copy response", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__benchmarkCopied)).toBe(expected.answer);
  expect(escapedWrites).toEqual([]);
  await testInfo.attach("streaming-metrics.json", { body: JSON.stringify(metrics), contentType: "application/json" });
});

test("trace toggles remain usable and upward scrolling is preserved while tokens arrive", async ({ page }, testInfo) => {
  const escapedWrites = await openBenchmark(page, 16_000);
  const trace = page.locator(".brainstorm-toggle").last();
  const reasoning = page.locator(".thinking-md-content").last();
  await expect(trace).toBeVisible();
  // Wait for the first reasoning-driven auto expansion before toggling; a
  // read-then-click on the initial connecting state races that transition.
  await expect(reasoning).toContainText("检查固定的合成材料");
  await expect(trace).toHaveAttribute("aria-expanded", "true");
  await trace.click();
  await expect(trace).toHaveAttribute("aria-expanded", "false");
  await trace.click();
  await expect(trace).toHaveAttribute("aria-expanded", "true");

  const answer = page.locator(".agent-turn .markdown-content").last();
  await expect.poll(async () => (await answer.textContent().catch(() => ""))?.length ?? 0, { timeout: 18_000 }).toBeGreaterThan(400);
  const scroller = page.locator(".conversation-scroll");
  await expect.poll(() => scroller.evaluate((node) => node.scrollHeight - node.clientHeight)).toBeGreaterThan(100);
  // Establish a lower reading position, then deliver real wheel input upward.
  // Only scrolling is controlled here; all token/state/render work remains real.
  await scroller.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  const box = await scroller.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.wheel(0, -10_000);
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);
  const before = await page.evaluate(() => window.__streamingBenchmark.snapshot());
  expect(before.finalDeltaMs).toBe(-1);
  const latest = page.getByRole("button", { name: "Back to latest", exact: true });
  await expect(latest).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__streamingBenchmark.snapshot().emittedFragments)).toBeGreaterThan(before.emittedFragments);
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);

  // Keyboard activation restores following without moving focus to the composer.
  await latest.focus();
  await page.keyboard.press("Enter");
  await expect(latest).toBeHidden();
  await expect(scroller).toBeFocused();
  const resumed = await page.evaluate(() => window.__streamingBenchmark.snapshot().emittedFragments);
  await expect.poll(() => page.evaluate(() => window.__streamingBenchmark.snapshot().emittedFragments)).toBeGreaterThan(resumed);
  await expect.poll(() => scroller.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(100);
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.wheel(0, -10_000);
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);
  await expect(latest).toBeVisible();

  const { metrics, expected } = await completedMetrics(page);
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);
  if (testInfo.project.name === "mobile") {
    await page.getByRole("tab", { name: /^Canvas/ }).click();
    await expect(scroller).toBeHidden();
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await expect(scroller).toBeVisible();
    await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);
    await expect(latest).toBeVisible();
  }
  const renderedReasoning = (await reasoning.textContent())!.replace(/\s+/g, "");
  expect(renderedReasoning).toBe(expected.reasoning.replace(/\s+/g, ""));
  const thinkingBody = page.locator(".thinking-md").last();
  const showMore = page.getByRole("button", { name: "Show more", exact: true });
  await expect(showMore).toHaveAttribute("aria-expanded", "false");
  const clampedHeight = await thinkingBody.evaluate((node) => node.clientHeight);
  await showMore.click();
  const showLess = page.getByRole("button", { name: "Show less", exact: true });
  await expect(showLess).toHaveAttribute("aria-expanded", "true");
  await expect.poll(() => thinkingBody.evaluate((node) => node.clientHeight)).toBeGreaterThan(clampedHeight);
  const naturalHeight = await reasoning.evaluate((node) => node.getBoundingClientRect().height);
  await expect.poll(() => thinkingBody.evaluate((node) => node.clientHeight)).toBeGreaterThanOrEqual(Math.floor(naturalHeight));
  await showLess.click();
  await expect(showMore).toHaveAttribute("aria-expanded", "false");
  await expect.poll(() => thinkingBody.evaluate((node) => node.clientHeight)).toBeLessThanOrEqual(clampedHeight + 1);
  await page.getByRole("button", { name: "Switch to Chinese", exact: true }).click();
  await expect(page.getByRole("button", { name: "展开更多", exact: true })).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "展开更多", exact: true }).click();
  await expect(page.getByRole("button", { name: "收起内容", exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "回到最新", exact: true })).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await page.locator(".jump-to-latest").evaluate((node) => parseFloat(getComputedStyle(node).transitionDuration))).toBeLessThan(0.001);
  await page.getByRole("button", { name: "回到最新", exact: true }).click();
  await expect(page.getByRole("button", { name: "回到最新", exact: true })).toBeHidden();
  await expect.poll(() => scroller.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight)).toBeLessThan(100);
  expect(escapedWrites).toEqual([]);
  await testInfo.attach("streaming-interaction-metrics.json", { body: JSON.stringify(metrics), contentType: "application/json" });
});
