import { expect, test, type Locator, type Page } from "@playwright/test";
import type { Agent, ArtifactResource, ExecutionEvent, Run, Thread } from "@alcuin/contracts";

const timestamp = "2026-09-08T12:00:00Z";
const reply = "I prepared a dashboard and a written brief. The example is documented here. [[cite:W1]]";
const html = '<h1>Capacity dashboard</h1><button id="add">Add one</button><output id="count">0</output><script>document.getElementById("add").onclick=()=>document.getElementById("count").textContent=String(Number(document.getElementById("count").textContent)+1)</script>';

function artifact(id: string, title: string, contentType: ArtifactResource["content_type"], content: string): ArtifactResource {
  return { id, title, content_type: contentType, content, workspace_id: "ws_demo", thread_id: "thr_artifact_e2e", source_run_id: "run_artifact_e2e", kind: "document", version: 1, created_at: timestamp, updated_at: timestamp };
}

type StreamWindow = Window & { __artifactEmit?: (events: ExecutionEvent[], done: boolean) => void; __artifactClipboard?: string };

async function mockArtifactStudio(page: Page, initialComplete = false, includeHistoricalArtifact = false, hydrationGate?: Promise<void>) {
  const dashboard = artifact("art_dashboard", "Capacity dashboard", "text/html", html);
  const brief = artifact("art_brief", "Capacity brief", "text/markdown", "# Capacity brief\n\n## Capacity findings\n\n- The current capacity is sufficient. [[cite:W1]]\n- Keep one unit available.");
  const resources = new Map<string, ArtifactResource>(initialComplete ? [[dashboard.id, dashboard], [brief.id, brief]] : []);
  if (includeHistoricalArtifact) resources.set("art_archived", { ...artifact("art_archived", "Archived capacity brief", "text/markdown", "## Earlier finding\n\nReserve capacity was limited. [[cite:W1]]"), source_run_id: "run_archived" });
  const downloads: string[] = [];
  const citationRequests: string[] = [];
  const edits: unknown[] = [];
  const thread: Thread = { id: "thr_artifact_e2e", workspace_id: "ws_demo", agent_id: "agt_artifact_e2e", agent_version_id: "av_artifact_e2e", title: "Artifact workspace", context: {}, created_at: timestamp };
  const agent: Agent = {
    id: thread.agent_id, workspace_id: "ws_demo", slug: "artifact-studio", name: "Artifact Studio", description: "Independent outputs", status: "published", current_version_id: thread.agent_version_id, published_version_id: thread.agent_version_id, version: 1, created_at: timestamp, updated_at: timestamp,
    definition: { schema_version: "1.0", identity: { name: "Artifact Studio", description: "Independent outputs", icon: "A" }, instructions: "Create useful artifacts.", model: { provider: "openai-compatible", model: "fixture-model" }, extensions: [], tools: [], knowledge: [], skills: [], rules: [], runtime: { adapter: "langgraph", max_steps: 10 }, policies: { mutating_tools: "ask", external_side_effects: "ask" }, context_policy: {}, output_schema: { type: "artifact" }, starter_prompts: [] },
  };
  let created = initialComplete;
  let completed = initialComplete;
  let sequence = 0;
  const events: ExecutionEvent[] = [];
  const event = (type: ExecutionEvent["type"], payload: Record<string, unknown>): ExecutionEvent => ({ id: `evt_artifact_${++sequence}`, run_id: "run_artifact_e2e", sequence, type, payload, timestamp });
  const run = (): Run => ({ id: "run_artifact_e2e", workspace_id: "ws_demo", thread_id: thread.id, agent_version_id: thread.agent_version_id, status: completed ? "completed" : "running", input: "Create a dashboard and a brief", created_at: timestamp, completed_at: completed ? timestamp : null });
  const source = () => event("citation.created", { citation_id: "W1", label: "Capacity reference", source: "Example documentation", locator: "https://example.com/capacity", snippet: "Capacity includes one reserve unit.", metadata: { kind: "web" } });
  if (initialComplete) events.push(event("run.started", {}), source(), event("message.delta", { delta: reply }), event("artifact.updated", { artifact: dashboard, streaming: false }), event("artifact.updated", { artifact: brief, streaming: false }), event("run.completed", {}));

  await page.addInitScript(() => {
    // Exercise the copy handler without changing the developer's system clipboard.
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text: string) => { (window as StreamWindow).__artifactClipboard = text; } } });
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (new URL(url, location.href).pathname === "/v1/runs/run_artifact_e2e/events") {
        const encoder = new TextEncoder();
        return new Response(new ReadableStream({ start(controller) {
          (window as StreamWindow).__artifactEmit = (items, done) => {
            for (const item of items) controller.enqueue(encoder.encode(`id: ${item.sequence}\nevent: ${item.type}\ndata: ${JSON.stringify(item)}\n\n`));
            if (done) { controller.enqueue(encoder.encode("data: [DONE]\n\n")); controller.close(); }
          };
        } }), { headers: { "Content-Type": "text/event-stream" } });
      }
      return original(input, init);
    };
  });
  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS" };
    const json = (body: unknown, status = 200) => route.fulfill({ status, headers, json: body });
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (path === "/v1/bootstrap") return json({ workspace: { id: "ws_demo", name: "Alcuin", created_at: timestamp }, agents: [agent], extensions: [], tools: [], knowledge_sources: [], threads: created ? [thread] : [], runs: created ? [run()] : [] });
    if (["/v1/skills", "/v1/rules", "/v1/providers", "/v1/tasks"].includes(path)) return json([]);
    if (path === "/v1/threads" && request.method() === "POST") { created = true; return json(thread); }
    if (path === `/v1/threads/${thread.id}`) { await hydrationGate; return json({ thread, messages: [], runs: created ? [run()] : [] }); }
    if (path.endsWith("/configuration")) return json({ thread_id: thread.id, workspace_id: "ws_demo", agent_version_id: thread.agent_version_id, revision: 1, active_skill_version_ids: [], manual_rule_version_ids: [], created_at: timestamp, updated_at: timestamp });
    if (path === `/v1/threads/${thread.id}/runs` && request.method() === "POST") return json(run());
    if (path === "/v1/runs/run_artifact_e2e") return json({ ...run(), events });
    if (path === "/v1/runs/run_archived/citations") {
      citationRequests.push(path);
      return json([{ id: "evt_archived_source", run_id: "run_archived", sequence: 2, type: "citation.created", timestamp, payload: { citation_id: "W1", label: "Archived capacity reference", source: "Archived documentation", locator: "https://example.com/capacity-archive", snippet: "The earlier record had no reserve units.", metadata: { kind: "web" } } }]);
    }
    if (path === "/v1/runs/run_artifact_e2e/citations") { citationRequests.push(path); return json(events.filter((event) => event.type === "citation.created")); }
    if (path.endsWith("/context")) return json({ detail: "Context trace unavailable in the isolated fixture" }, 404);
    if (path.endsWith("/artifacts")) return json([...resources.values()].reverse());
    const match = /^\/v1\/artifacts\/([^/]+)(\/download)?$/.exec(path);
    if (match) {
      const current = resources.get(match[1]!);
      if (!current) return json({ detail: "Artifact not found" }, 404);
      if (match[2]) {
        const format = new URL(request.url()).searchParams.get("format")!;
        downloads.push(`${current.id}:${format}`);
        return route.fulfill({ headers: { ...headers, "Content-Disposition": `attachment; filename="artifact.${format}"` }, contentType: format === "docx" ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document" : format === "html" ? "text/html" : "text/markdown", body: format === "docx" ? Buffer.from([80, 75, 3, 4]) : current.content });
      }
      if (request.method() === "PATCH") {
        const payload = request.postDataJSON(); edits.push(payload);
        const next = { ...current, ...payload, version: current.version + 1 };
        resources.set(current.id, next);
        return json(next);
      }
      return json(current);
    }
    return json({ detail: `Unhandled fixture route: ${path}` }, 404);
  });
  const emit = async (items: ExecutionEvent[], done = false) => {
    for (const item of items) { if (item.type === "artifact.updated") { const value = item.payload.artifact as ArtifactResource; resources.set(value.id, value); } }
    events.push(...items);
    if (done) completed = true;
    await page.evaluate(({ items, done }) => (window as StreamWindow).__artifactEmit!(items, done), { items, done });
  };
  return { dashboard, brief, downloads, citationRequests, edits, event, source, emit };
}

test("Studio streams two independent artifacts, keeps its reply separate, and supports edits and downloads", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "The focused narrow-screen test covers mobile interaction");
  const fixture = await mockArtifactStudio(page);
  await page.goto("/studio");
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).fontFamily)).toMatch(/Geist/i);
  const composer = page.getByPlaceholder("Message Artifact Studio…");
  await composer.fill("Create a dashboard and a brief");
  await composer.press("Enter");
  await expect.poll(() => page.evaluate(() => typeof (window as StreamWindow).__artifactEmit)).toBe("function");
  await fixture.emit([fixture.event("run.started", {}), fixture.event("message.delta", { delta: reply }), fixture.source()]);
  await expect(page.locator(".conversation-pane")).toContainText("I prepared a dashboard and a written brief.");
  await expect(page.locator(".context-canvas")).toBeHidden();

  await fixture.emit([fixture.event("artifact.updated", { artifact: { ...fixture.dashboard, content: "<h1>Preparing capacity dashboard</h1>" }, streaming: true })]);
  await expect(page.locator(".context-canvas")).toBeVisible();
  await expect(page.frameLocator(".artifact-html-frame").getByRole("heading")).toHaveText("Preparing capacity dashboard");
  await expect(page.getByRole("button", { name: "Download HTML" })).toBeDisabled();
  await fixture.emit([fixture.event("artifact.updated", { artifact: { ...fixture.dashboard, version: 2 }, streaming: false }), fixture.event("artifact.updated", { artifact: fixture.brief, streaming: false }), fixture.event("run.completed", {})], true);
  await expect(page.getByRole("button", { name: "Download Word" })).toBeEnabled();
  await expect(page.getByRole("tab", { name: "Capacity dashboard", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Capacity brief", exact: true })).toBeVisible();
  await expect(page.locator(".artifact-document")).toContainText("Capacity findings");
  await expect(page.locator(".artifact-workspace")).not.toContainText("I prepared a dashboard");
  await expect(page.locator(".conversation-pane")).not.toContainText("The current capacity is sufficient");
  await page.locator(".conversation-pane").getByRole("button", { name: "View source 1: Capacity reference" }).click();
  await expect(page.getByRole("dialog")).toContainText("Capacity includes one reserve unit.");
  await page.keyboard.press("Escape");
  await page.locator(".artifact-document").getByRole("button", { name: "View source 1: Capacity reference" }).click();
  await expect(page.getByRole("dialog")).toContainText("Capacity includes one reserve unit.");
  await expect(page.locator(".artifact-document")).not.toContainText("[[cite:");
  await page.keyboard.press("Escape");
  expect(fixture.citationRequests).toEqual([]);

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox", { name: "Content", exact: true }).fill("## Edited capacity brief\n\nReserved capacity: one unit.");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".artifact-document")).toContainText("Edited capacity brief");
  const wordPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Word" }).click();
  expect((await wordPromise).suggestedFilename()).toBe("Capacity brief.docx");
  expect(fixture.downloads).toContain("art_brief:docx");
  expect(fixture.edits).toContainEqual({ expected_version: 1, title: "Capacity brief", content: "## Edited capacity brief\n\nReserved capacity: one unit." });

  await page.getByRole("tab", { name: "Capacity dashboard", exact: true }).click();
  const preview = page.frameLocator(".artifact-html-frame");
  await preview.getByRole("button", { name: "Add one" }).click();
  await expect(preview.locator("#count")).toHaveText("1");
  await page.getByRole("tab", { name: "Source code" }).click();
  await expect(page.locator(".artifact-source-code")).toContainText('id="count"');
  await page.getByRole("tab", { name: "Source code" }).press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Preview", exact: true })).toBeFocused();
  await expect(preview.locator("#count")).toHaveText("1");
  const htmlPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download HTML" }).click();
  expect((await htmlPromise).suggestedFilename()).toBe("Capacity dashboard.html");
  expect(fixture.downloads).toContain("art_dashboard:html");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(preview.locator("#count")).toHaveText("1");
  await expect.poll(() => preview.locator("html").evaluate((node) => getComputedStyle(node).colorScheme)).toBe("dark");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: testInfo.outputPath("artifact-studio-dark.png"), fullPage: true });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox", { name: "HTML source", exact: true }).fill(html.replace("Capacity dashboard", "Updated capacity dashboard"));
  await page.getByRole("textbox", { name: "HTML source", exact: true }).press("Meta+s");
  await expect(page.frameLocator(".artifact-html-frame").getByRole("heading")).toHaveText("Updated capacity dashboard");
  await page.reload();
  await page.getByRole("tab", { name: "Capacity dashboard", exact: true }).click();
  await expect(page.frameLocator(".artifact-html-frame").getByRole("heading")).toHaveText("Updated capacity dashboard");
  await expect(page.getByRole("tab", { name: "Capacity brief", exact: true })).toBeVisible();
});

test("restores independent artifacts on narrow screens with Chinese, dark mode and reduced motion", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockArtifactStudio(page, true);
  await page.goto("/studio?thread=thr_artifact_e2e");
  await page.getByRole("button", { name: "Switch to Chinese" }).click();
  await page.getByRole("button", { name: "切换主题" }).click();
  await page.getByRole("tab", { name: /^画布/ }).click();
  await expect(page.locator(".conversation-pane")).toBeHidden();
  await expect(page.getByRole("tab", { name: "Capacity brief", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Capacity dashboard", exact: true }).click();
  await expect(page.getByRole("tab", { name: "预览", exact: true })).toBeVisible();
  await page.frameLocator(".artifact-html-frame").getByRole("button", { name: "Add one" }).click();
  await expect(page.frameLocator(".artifact-html-frame").locator("#count")).toHaveText("1");
  await expect(page.getByRole("button", { name: "下载 HTML" })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(await page.locator(".artifact-html-panel:visible").evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: testInfo.outputPath("artifact-studio-mobile.png"), fullPage: true });
});

test("historical Markdown artifacts lazily resolve citations from their own Run", async ({ page }) => {
  const fixture = await mockArtifactStudio(page, true, true);
  await page.goto("/studio?thread=thr_artifact_e2e");
  await expect(page.getByRole("heading", { name: "Artifact Studio", exact: true })).toBeVisible();
  if (await page.getByRole("tab", { name: /^Canvas/ }).isVisible()) await page.getByRole("tab", { name: /^Canvas/ }).click();
  await page.getByRole("tab", { name: "Archived capacity brief", exact: true }).click();
  const document = page.locator(".artifact-document");
  await expect(document).not.toContainText("[[cite:");
  await expect(document.getByRole("button", { name: "View source 1: Capacity reference" })).toHaveCount(0);
  expect(fixture.citationRequests).toEqual([]);
  await document.getByRole("button", { name: "View cited sources", exact: true }).first().click();
  await expect(page.getByRole("dialog")).toContainText("The earlier record had no reserve units.");
  await expect(page.getByRole("dialog")).not.toContainText("Capacity includes one reserve unit.");
  await expect(document.getByRole("button", { name: "View source 1: Archived capacity reference" })).toBeVisible();
  expect(fixture.citationRequests).toEqual(["/v1/runs/run_archived/citations"]);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Capacity brief", exact: true }).click();
  await expect(document.getByRole("button", { name: "View source 1: Capacity reference" })).toBeVisible();
});

test("Artifact document headings do not duplicate its title and body uses the UI font", async ({ page }) => {
  await mockArtifactStudio(page, true);
  await page.goto("/studio?thread=thr_artifact_e2e");
  await expect(page.getByRole("heading", { name: "Artifact Studio", exact: true })).toBeVisible();
  if (await page.getByRole("tab", { name: /^Canvas/ }).isVisible()) await page.getByRole("tab", { name: /^Canvas/ }).click();
  await page.getByRole("tab", { name: "Capacity brief", exact: true }).click();
  const document = page.locator(".artifact-document");
  await expect(document.getByRole("heading", { name: "Capacity brief", exact: true })).toHaveCount(1);
  await expect(document.getByRole("heading", { name: "Capacity findings", exact: true })).toHaveCount(1);
  expect(await document.locator(".markdown-artifact").evaluate((node) => getComputedStyle(node).fontFamily)).toMatch(/Geist/i);
  expect(await document.locator(":scope > h2").evaluate((node) => getComputedStyle(node).fontFamily)).toMatch(/Instrument/i);
});

test("Artifact copy exports portable Markdown references while preserving raw HTML", async ({ page }) => {
  await mockArtifactStudio(page, true);
  await page.goto("/studio?thread=thr_artifact_e2e");
  await page.locator(".conversation-pane").getByRole("button", { name: "Copy response", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as StreamWindow).__artifactClipboard)).toContain("https://example.com/capacity");
  expect(await page.evaluate(() => (window as StreamWindow).__artifactClipboard)).not.toContain("[[cite:");
  expect(await page.evaluate(() => (window as StreamWindow).__artifactClipboard)).toContain("I prepared a dashboard");
  if (await page.getByRole("tab", { name: /^Canvas/ }).isVisible()) await page.getByRole("tab", { name: /^Canvas/ }).click();
  await page.getByRole("tab", { name: "Capacity brief", exact: true }).click();
  await page.getByRole("button", { name: "Copy artifact", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as StreamWindow).__artifactClipboard)).toContain("https://example.com/capacity");
  expect(await page.evaluate(() => (window as StreamWindow).__artifactClipboard)).not.toContain("[[cite:");
  await expect.poll(() => page.evaluate(() => (window as StreamWindow).__artifactClipboard)).toContain("Capacity findings");
  await page.getByRole("tab", { name: "Capacity dashboard", exact: true }).click();
  await page.getByRole("button", { name: "Copy artifact", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as StreamWindow).__artifactClipboard)).toBe(html);

  await expect(page.getByText("Response copied", { exact: true })).toBeHidden();
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error("Clipboard unavailable"); }; });
  if (await page.getByRole("tab", { name: "Chat", exact: true }).isVisible()) await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await page.locator(".conversation-pane").getByRole("button", { name: "Copy response", exact: true }).click();
  await expect(page.getByText("Copy failed. Try again.", { exact: true })).toBeVisible();
  await expect(page.getByText("Response copied", { exact: true })).toBeHidden();
});

async function expectPopoverAnchored(page: Page, marker: Locator) {
  const popup = page.locator(".citation-preview");
  await expect(popup).toBeVisible();
  await popup.evaluate(async (element) => { await Promise.all(element.getAnimations().map((animation) => animation.finished)); });
  await expect.poll(async () => {
    const anchor = await marker.boundingBox();
    const box = await popup.boundingBox();
    const viewport = page.viewportSize();
    if (!anchor || !box || !viewport) return false;
    const left = Math.max(12, Math.min(anchor.x, viewport.width - box.width - 12));
    const below = anchor.y + anchor.height + 8;
    const top = below + box.height <= viewport.height - 12 ? below : Math.max(12, anchor.y - box.height - 8);
    return Math.abs(box.x - left) < 2 && Math.abs(box.y - top) < 2;
  }).toBe(true);
}

test("citation popovers stay anchored after opening, history updates, and responsive layout changes", async ({ page }) => {
  await mockArtifactStudio(page, true, true);
  await page.goto("/studio?thread=thr_artifact_e2e");
  const responseMarker = page.locator(".conversation-pane .citation-inline").first();
  await responseMarker.click();
  await expectPopoverAnchored(page, responseMarker);
  await page.keyboard.press("Escape");
  await expect(responseMarker).toBeFocused();

  if (await page.getByRole("tab", { name: /^Canvas/ }).isVisible()) await page.getByRole("tab", { name: /^Canvas/ }).click();
  await page.getByRole("tab", { name: "Archived capacity brief", exact: true }).click();
  const artifactMarker = page.locator(".artifact-document .citation-inline").first();
  await artifactMarker.click();
  await expect(page.getByRole("dialog")).toContainText("The earlier record had no reserve units.");
  await expectPopoverAnchored(page, artifactMarker);
  await page.keyboard.press("Escape");
  await expect(artifactMarker).toBeFocused();

  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: /^Canvas/ }).click();
  await artifactMarker.click();
  await expectPopoverAnchored(page, artifactMarker);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.keyboard.press("Escape");
  await expect(artifactMarker).toBeFocused();
});

test("late thread hydration preserves the mobile Canvas selection", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let releaseHydration!: () => void;
  const hydrationGate = new Promise<void>((resolve) => { releaseHydration = resolve; });
  await mockArtifactStudio(page, true, false, hydrationGate);
  await page.goto("/studio?thread=thr_artifact_e2e");
  const canvas = page.getByRole("tab", { name: /^Canvas/ });
  await canvas.click();
  await expect(canvas).toHaveAttribute("aria-selected", "true");
  releaseHydration();
  await expect(page.getByRole("tab", { name: "Capacity brief", exact: true })).toBeVisible();
  await expect(canvas).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".conversation-pane")).toBeHidden();
});
