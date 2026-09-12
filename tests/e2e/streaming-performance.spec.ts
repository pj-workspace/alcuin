import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const fixture = readFileSync(resolve(process.cwd(), "tests/fixtures/streaming-performance.cjs"), "utf8");

declare global {
  interface Window {
    __benchmarkCopied?: string;
    __streamingBenchmark: {
      expected: { reasoning: string; answer: string; fragments: number };
      snapshot(): Record<string, number>;
    };
  }
}

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
  const escapedWrites = await openBenchmark(page, 12_000);
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
  await expect.poll(() => page.evaluate(() => window.__streamingBenchmark.snapshot().emittedFragments)).toBeGreaterThan(before.emittedFragments);
  await expect.poll(() => scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);

  const { metrics, expected } = await completedMetrics(page);
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeLessThan(5);
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
  expect(escapedWrites).toEqual([]);
  await testInfo.attach("streaming-interaction-metrics.json", { body: JSON.stringify(metrics), contentType: "application/json" });
});
