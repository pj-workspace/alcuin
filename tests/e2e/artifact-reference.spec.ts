import { createHash, randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import type { ArtifactReferenceMessagePart, ArtifactResource, Run, Thread } from "../../packages/contracts/src/index";

const api = "http://localhost:8100";
const headers = { "X-Alcuin-Workspace": "ws_demo" };

test("canvas draft survives closing and reload, reviews conflicts without losing edits", async ({ page, request }, testInfo) => {
  test.setTimeout(60_000);
  const { thread, artifact } = await createDocument(request);
  await setup(page, thread, "en");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await openDocument(page, artifact, "en");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const editor = page.getByRole("textbox", { name: "Content", exact: true });
  const draft = `My unsaved work ${randomUUID()}`;
  await editor.fill(draft);
  await page.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  const banner = page.locator(".artifact-draft-banner");
  await expect(banner).toContainText("Unsaved draft");
  await expect(banner).toHaveCSS("animation-name", "artifact-draft-arrive");
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await expect(page.getByRole("tab", { name: `${artifact.title} •`, exact: true, includeHidden: true })).toHaveCount(1);
  if (await page.evaluate(() => matchMedia("(max-width: 820px)").matches)) await page.locator("#mobile-canvas-tab").click();
  else await expect(async () => {
    if (await page.locator("#studio-canvas-panel").getAttribute("aria-hidden") !== "false") {
      const open = page.getByRole("button", { name: "Open canvas", exact: true });
      if (await open.isVisible()) await open.click({ timeout: 500 });
    }
    await expect(page.locator("#studio-canvas-panel")).toHaveAttribute("aria-hidden", "false", { timeout: 500 });
  }).toPass({ timeout: 10_000 });
  await expect(page.locator("#canvas-tab-artifact")).toBeInViewport({ ratio: 1 });
  await page.locator("#canvas-tab-artifact").click();
  await page.getByRole("button", { name: "Resume editing", exact: true }).click();
  await expect(editor).toHaveValue(draft);
  await patchDocument(request, artifact, "Newer remote content");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".artifact-conflict-review")).toBeVisible();
  await expect(editor).toHaveValue(draft);
  await page.getByText("Review saved content", { exact: true }).click();
  await expect(page.locator(".artifact-conflict-content pre")).toHaveText("Newer remote content");
  await expect.poll(() => page.locator(".artifact-conflict-review details").evaluate((element) => element.getAnimations({ subtree: true }).filter((animation) => animation.playState === "running").length)).toBe(0);
  await page.getByRole("button", { name: "Keep draft and continue", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("draft-conflict.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Keep draft and continue", exact: true }).click();
  await expect(editor).toHaveValue(draft);
  expect(await (await request.get(`${api}/v1/artifacts/${artifact.id}`, { headers })).json()).toMatchObject({ content: "Newer remote content" });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(editor).toHaveCount(0);
  await expect(banner).toHaveCount(0);
  expect(await (await request.get(`${api}/v1/artifacts/${artifact.id}`, { headers })).json()).toMatchObject({ content: draft });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await editor.fill("Deliberately discarded edit");
  await page.getByRole("button", { name: "Keep draft and close", exact: true }).click();
  await page.getByRole("button", { name: "Discard draft", exact: true }).click();
  await expect(banner).toHaveCount(0);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(editor).toHaveValue(draft);
});
type Locale = "en" | "zh";
type ThreadDetail = { messages: Array<{ role: string; parts: Array<{ type: string } & Partial<ArtifactReferenceMessagePart>> }>; runs: Run[] };

async function readThread(request: APIRequestContext, id: string): Promise<ThreadDetail> {
  const response = await request.get(`${api}/v1/threads/${id}`, { headers });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function createThread(request: APIRequestContext, title: string): Promise<Thread> {
  const response = await request.post(`${api}/v1/threads`, { headers, data: { agent_id: "agt_operations", title, context: {} } });
  expect(response.status()).toBe(201);
  return response.json();
}

async function createDocument(request: APIRequestContext) {
  const thread = await createThread(request, `Document reference ${randomUUID()}`);
  // This marker selects a deterministic response from the local provider fixture.
  // The API, runtime artifact parser, persistence and version checks remain real.
  const response = await request.post(`${api}/v1/threads/${thread.id}/runs`, {
    headers, data: { input: "Create a document [artifact-reference-fixture]" },
  });
  expect(response.status()).toBe(202);
  const run = await response.json() as Run;
  await expect.poll(async () => {
    const snapshot = await request.get(`${api}/v1/runs/${run.id}`, { headers });
    expect(snapshot.ok()).toBe(true);
    return (await snapshot.json() as Run).status;
  }, { timeout: 20_000 }).toBe("completed");
  const resources = await request.get(`${api}/v1/threads/${thread.id}/artifacts`, { headers });
  expect(resources.ok()).toBe(true);
  const artifacts = await resources.json() as ArtifactResource[];
  expect(artifacts).toHaveLength(1);
  return { thread, artifact: artifacts[0]! };
}

async function setup(page: Page, thread: Thread, locale: Locale) {
  await page.addInitScript((language) => {
    localStorage.setItem("alcuin-locale", language);
    localStorage.setItem("alcuin-theme", "dark");
  }, locale);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/studio?thread=${thread.id}`);
  await expect(page.locator(".sidebar-footer")).toHaveAttribute("data-state", "connected");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("textbox", { name: locale === "zh" ? "消息输入" : "Message input", exact: true })).toBeVisible();
}

async function openDocument(page: Page, artifact: ArtifactResource, locale: Locale) {
  // Wait for resource hydration first: it can open the canvas automatically.
  // A one-shot isVisible check before hydration races the Open → Close toggle.
  await expect(page.getByRole("tab", { name: artifact.title, exact: true, includeHidden: true })).toHaveCount(1);
  const canvas = page.locator("#studio-canvas-panel");
  if (await page.evaluate(() => matchMedia("(max-width: 820px)").matches)) {
    await page.locator("#mobile-canvas-tab").click();
  } else {
    await expect(async () => {
      if (await canvas.getAttribute("aria-hidden") !== "false") {
        const open = page.getByRole("button", { name: locale === "zh" ? "打开画布" : "Open canvas", exact: true });
        if (await open.isVisible()) await open.click({ timeout: 500 });
      }
      await expect(canvas).toHaveAttribute("aria-hidden", "false", { timeout: 500 });
    }).toPass({ timeout: 10_000 });
  }
  await expect(canvas).toHaveAttribute("aria-hidden", "false");
  await expect(canvas).not.toHaveAttribute("inert", "");
  await expect(canvas).toBeVisible();
  await expect(canvas).toBeInViewport();
  const artifactTab = canvas.locator("#canvas-tab-artifact");
  await expect(artifactTab).toBeInViewport({ ratio: 1 });
  await artifactTab.click({ timeout: 5_000 });
  await canvas.getByRole("tab", { name: artifact.title, exact: true }).click({ timeout: 5_000 });
  const document = canvas.locator(".artifact-document");
  await expect(document).toBeVisible();
  await expect(document.locator(":scope > h2")).toHaveText(artifact.title);
  // Measure the content's position, not its total paper-like height: a short
  // document may intentionally have blank space below its content. The CTA
  // must not stretch into a full-height grid row above the actual document.
  // Compare normal-flow siblings: the sticky toolbar can legitimately overlap
  // the document after saving an editor that scrolled the canvas panel.
  await expect(async () => {
    const geometry = await canvas.evaluate((element) => {
      const action = element.querySelector(".artifact-use-button")?.getBoundingClientRect();
      const document = element.querySelector(".artifact-document")?.getBoundingClientRect();
      const heading = element.querySelector(".artifact-document > h2")?.getBoundingClientRect();
      if (!action || !document || !heading) return null;
      return {
        canvasScrollTop: element.querySelector("#canvas-panel-artifact")?.scrollTop,
        actionHeight: action.height,
        actionBottom: action.bottom,
        documentTop: document.top,
        documentGap: document.top - action.bottom,
        headingInset: heading.top - document.top,
      };
    });
    const diagnostic = `Artifact layout geometry: ${JSON.stringify(geometry)}`;
    expect(geometry, diagnostic).not.toBeNull();
    expect(geometry!.actionHeight, diagnostic).toBeGreaterThan(0);
    expect(geometry!.actionHeight, diagnostic).toBeLessThanOrEqual(64);
    expect(geometry!.documentGap, diagnostic).toBeGreaterThanOrEqual(0);
    expect(geometry!.documentGap, diagnostic).toBeLessThanOrEqual(32);
    expect(geometry!.headingInset, diagnostic).toBeGreaterThanOrEqual(0);
    expect(geometry!.headingInset, diagnostic).toBeLessThanOrEqual(120);
  }).toPass({ timeout: 5_000 });
}

async function attachDocument(page: Page, artifact: ArtifactResource, locale: Locale) {
  await openDocument(page, artifact, locale);
  await page.getByRole("button", { name: locale === "zh" ? "基于此文档继续" : "Use in conversation", exact: true }).click();
  await expect(page.locator(".conversation-pane")).toBeVisible();
  await expect(page.getByTestId("composer-artifact-reference")).toContainText(artifact.title);
}

async function patchDocument(request: APIRequestContext, artifact: ArtifactResource, content: string): Promise<ArtifactResource> {
  const response = await request.patch(`${api}/v1/artifacts/${artifact.id}`, {
    headers, data: { expected_version: artifact.version, title: artifact.title, content },
  });
  expect(response.ok()).toBe(true);
  return response.json();
}

for (const locale of ["en", "zh"] as const) {
  test(`saved document reference sends its version and preserves the submitted snapshot (${locale})`, async ({ page, request }, testInfo) => {
    test.setTimeout(60_000);
    const { thread, artifact } = await createDocument(request);
    const savedContent = `# Saved document B\n\nUnique saved finding ${randomUUID()}.\n\n<em>This stays literal in the reference viewer.</em>`;
    await setup(page, thread, locale);
    await openDocument(page, artifact, locale);
    await page.getByRole("button", { name: locale === "zh" ? "编辑" : "Edit", exact: true }).click();
    const editor = page.getByRole("textbox", { name: locale === "zh" ? "内容" : "Content", exact: true });
    await expect(editor).toBeVisible();
    const attach = page.getByRole("button", { name: locale === "zh" ? "基于此文档继续" : "Use in conversation", exact: true });
    await expect(attach).toHaveCount(0);
    await editor.fill(savedContent);
    const saved = page.waitForResponse((response) => response.url().endsWith(`/v1/artifacts/${artifact.id}`) && response.request().method() === "PATCH");
    await page.getByRole("button", { name: locale === "zh" ? "保存" : "Save", exact: true }).click();
    const savedResponse = await saved;
    expect(savedResponse.ok()).toBe(true);
    const versionB = await savedResponse.json() as ArtifactResource;
    expect(versionB.content).toBe(savedContent);
    await attachDocument(page, versionB, locale);
    await expect(page.getByTestId("composer-artifact-reference")).toContainText(locale === "zh" ? "已保存的内容会随下一条消息发送，原文不会被覆盖。" : "Saved content will be sent with your next message. The original stays unchanged.");
    await expect(page.getByRole("group", { name: locale === "zh" ? "工作模式" : "Work mode" }).getByRole("button", { name: locale === "zh" ? "计划" : "Plan", exact: true })).toBeDisabled();
    const prompt = `Explain the saved finding ${randomUUID()}`;
    const composer = page.getByRole("textbox", { name: locale === "zh" ? "消息输入" : "Message input", exact: true });
    await composer.fill(prompt);
    const sent = page.waitForResponse((response) => response.url().endsWith(`/v1/threads/${thread.id}/runs`) && response.request().method() === "POST");
    const canonicalMessages = page.waitForResponse((response) => new URL(response.url()).pathname === `/v1/threads/${thread.id}/messages` && response.request().method() === "GET");
    await composer.press("Enter");
    const sentResponse = await sent;
    expect(sentResponse.status()).toBe(202);
    const payload = sentResponse.request().postDataJSON();
    expect(payload.input).toBe(prompt);
    expect(payload.artifact_references).toEqual([{ artifact_id: artifact.id, expected_version: versionB.version }]);
    expect(JSON.stringify(payload)).not.toContain(savedContent);
    await expect(page.getByTestId("composer-artifact-reference")).toHaveCount(0);
    const userSnapshot = (await readThread(request, thread.id)).messages
      .filter((message) => message.role === "user").flatMap((message) => message.parts)
      .find((part) => part.type === "artifact_reference");
    expect(userSnapshot).toMatchObject({
      artifact_id: artifact.id, version: versionB.version, title: artifact.title,
      content: savedContent, sha256: createHash("sha256").update(savedContent).digest("hex"),
    });
    // Accepted messages must replace the optimistic reference with the canonical
    // server snapshot, including its digest, without needing a page reload.
    expect((await canonicalMessages).ok()).toBe(true);
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await page.getByTestId("message-artifact-reference").click();
    const acceptedDialog = page.getByRole("dialog", { name: artifact.title, exact: true });
    await expect(acceptedDialog.locator('code[title="SHA-256"]')).toHaveText(`SHA-256 · ${userSnapshot!.sha256}`);
    expect(await acceptedDialog.locator("pre").textContent()).toBe(savedContent);
    await page.keyboard.press("Escape");
    await expect(acceptedDialog).toHaveCount(0);
    const versionC = await patchDocument(request, versionB, `# Later document C\n\n${randomUUID()}`);
    await page.reload();
    await page.getByTestId("message-artifact-reference").click();
    const dialog = page.getByRole("dialog", { name: artifact.title, exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(locale === "zh" ? "引用的文档" : "Referenced document", { exact: true })).toBeVisible();
    expect(await dialog.locator("pre").textContent()).toBe(savedContent);
    await expect(dialog.locator("pre em")).toHaveCount(0);
    await expect(dialog).not.toContainText(versionC.content);
    await page.getByRole("button", { name: locale === "zh" ? "关闭文档引用" : "Close document reference", exact: true }).click();
    await expect(dialog).toBeHidden();
    const original = await request.get(`${api}/v1/artifacts/${artifact.id}`, { headers });
    expect(original.ok()).toBe(true);
    expect(await original.json()).toMatchObject({ id: artifact.id, content: versionC.content, version: versionC.version });
    expect((await readThread(request, thread.id)).messages.flatMap((message) => message.parts).find((part) => part.type === "artifact_reference")).toEqual(userSnapshot);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`document-reference-${locale}.png`), fullPage: true });
  });
}

test("stale references keep the draft, can be removed, and do not leak into another thread", async ({ page, request }) => {
  test.setTimeout(60_000);
  const { thread, artifact } = await createDocument(request);
  const other = await createThread(request, `Reference destination ${randomUUID()}`);
  await setup(page, thread, "en");
  await attachDocument(page, artifact, "en");
  await patchDocument(request, artifact, "# A newer saved document\n\nConcurrent change.");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  const prompt = `Keep this unsent prompt ${randomUUID()}`;
  await composer.fill(prompt);
  const attempts: unknown[] = [];
  page.on("request", (outgoing) => {
    if (outgoing.url().endsWith(`/v1/threads/${thread.id}/runs`) && outgoing.method() === "POST") attempts.push(outgoing.postDataJSON());
  });
  const rejected = page.waitForResponse((response) => response.url().endsWith(`/v1/threads/${thread.id}/runs`) && response.request().method() === "POST");
  await composer.press("Enter");
  expect((await rejected).status()).toBe(409);
  await expect(composer).toHaveValue(prompt);
  await expect(page.getByTestId("composer-artifact-reference")).toContainText(artifact.title);
  await expect(page.getByText(/document changed.*attach.*again/i)).toBeVisible();
  expect((await readThread(request, thread.id)).runs).toHaveLength(1);
  await page.getByRole("button", { name: "Remove document reference", exact: true }).click();
  await expect(page.getByTestId("composer-artifact-reference")).toHaveCount(0);
  await expect(composer).toHaveValue(prompt);
  await attachDocument(page, artifact, "en");
  const destination = page.getByRole("button", { name: other.title, exact: true });
  if (!await destination.isVisible()) await page.getByRole("button", { name: "Toggle sidebar", exact: true }).first().click();
  await destination.click();
  await expect(page).toHaveURL(new RegExp(`thread=${other.id}`));
  await expect(page.getByTestId("composer-artifact-reference")).toHaveCount(0);
  await expect(composer).toHaveValue("");
  const nextRun = page.waitForResponse((response) => response.url().endsWith(`/v1/threads/${other.id}/runs`) && response.request().method() === "POST");
  await composer.fill("Describe the current workspace");
  await composer.press("Enter");
  const nextResponse = await nextRun;
  expect(nextResponse.status()).toBe(202);
  expect(nextResponse.request().postDataJSON().artifact_references ?? []).toEqual([]);
  expect((await readThread(request, other.id)).messages.flatMap((message) => message.parts).filter((part) => part.type === "artifact_reference")).toEqual([]);
  expect(attempts).toHaveLength(1);
});

test("a stale reference can be reattached at the refreshed version without reloading the page", async ({ page, request }) => {
  test.setTimeout(60_000);
  const { thread, artifact } = await createDocument(request);
  await setup(page, thread, "en");
  await attachDocument(page, artifact, "en");
  const latest = await patchDocument(request, artifact, `# Freshly saved version\n\n${randomUUID()}`);
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  const prompt = `Explain this saved version ${randomUUID()}`;
  await composer.fill(prompt);
  const rejected = page.waitForResponse((response) => response.url().endsWith(`/v1/threads/${thread.id}/runs`) && response.request().method() === "POST");
  await composer.press("Enter");
  expect((await rejected).status()).toBe(409);
  await expect(page.getByText("This document changed. Attach it again from the canvas before sending.", { exact: true })).toBeVisible();
  await expect(composer).toHaveValue(prompt);
  await expect(page.getByTestId("composer-artifact-reference")).toBeVisible();
  await openDocument(page, latest, "en");
  await expect(page.locator(".artifact-document")).toContainText("Freshly saved version");
  await page.getByRole("button", { name: "Use in conversation", exact: true }).click();
  await expect(composer).toHaveValue(prompt);
  const accepted = page.waitForResponse((response) => response.url().endsWith(`/v1/threads/${thread.id}/runs`) && response.request().method() === "POST");
  await composer.press("Enter");
  const response = await accepted;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON().artifact_references).toEqual([{ artifact_id: artifact.id, expected_version: latest.version }]);
  const detail = await readThread(request, thread.id);
  expect(detail.runs).toHaveLength(2);
  expect(detail.messages.flatMap((message) => message.parts).find((part) => part.type === "artifact_reference"))
    .toMatchObject({ artifact_id: artifact.id, version: latest.version, content: latest.content });
  await expect(composer).toHaveValue("");
  await expect(page.getByTestId("composer-artifact-reference")).toHaveCount(0);
});

test("a late acceptance from another thread preserves the current draft and document reference", async ({ page, request }) => {
  test.setTimeout(60_000);
  const first = await createDocument(request);
  const second = await createDocument(request);
  await setup(page, first.thread, "en");
  await attachDocument(page, first.artifact, "en");
  let release!: () => void;
  let didAccept!: (run: Run) => void;
  let rejectAcceptance!: (error: unknown) => void;
  let finishRoute!: () => void;
  let routeStarted = false;
  let originalFailure: unknown;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const acceptedAtServer = new Promise<Run>((resolve, reject) => { didAccept = resolve; rejectAcceptance = reject; });
  // A failed route must reject the server-acceptance wait, even if the test is
  // still finishing its Enter key action when the request fails.
  void acceptedAtServer.catch(() => undefined);
  const routeFinished = new Promise<void>((resolve) => { finishRoute = resolve; });
  const postURL = `${api}/v1/threads/${first.thread.id}/runs`;
  await page.route(postURL, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    routeStarted = true;
    try {
      // Exactly one real POST is forwarded. Only its response delivery is gated.
      const response = await route.fetch({ timeout: 10_000 });
      expect(response.status()).toBe(202);
      didAccept(await response.json() as Run);
      await gate;
      await route.fulfill({ response });
    } catch (error) {
      rejectAcceptance(error);
      throw error;
    } finally {
      finishRoute();
    }
  });
  try {
    const composer = page.getByRole("textbox", { name: "Message input", exact: true });
    await composer.fill(`Explain the first saved document ${randomUUID()}`);
    await composer.press("Enter");
    const firstRun = await boundedWait(acceptedAtServer, 12_000, "The gated POST was not accepted by the server");
    const streams: string[] = [];
    page.on("request", (outgoing) => {
      if (new URL(outgoing.url()).pathname === `/v1/runs/${firstRun.id}/events`) streams.push(outgoing.url());
    });
    const destination = page.getByRole("button", { name: second.thread.title, exact: true });
    if (!await destination.isVisible()) await page.getByRole("button", { name: "Toggle sidebar", exact: true }).first().click();
    await destination.click();
    await expect(page).toHaveURL(new RegExp(`thread=${second.thread.id}`));
    await attachDocument(page, second.artifact, "en");
    const nextDraft = `Keep the second document draft ${randomUUID()}`;
    await composer.fill(nextDraft);
    const delivered = page.waitForResponse((response) => response.url() === postURL && response.request().method() === "POST");
    release();
    expect((await delivered).status()).toBe(202);
    // Allow React to flush the accepted-response callback before checking scope.
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page).toHaveURL(new RegExp(`thread=${second.thread.id}`));
    await expect(composer).toHaveValue(nextDraft);
    await expect(page.getByTestId("composer-artifact-reference")).toBeVisible();
    await page.getByTestId("composer-artifact-reference").getByRole("button", { name: "View referenced document", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: second.artifact.title, exact: true });
    await expect(dialog.locator("footer")).toContainText(second.artifact.id);
    await expect(dialog.locator("footer")).not.toContainText(first.artifact.id);
    expect(streams).toEqual([]);
    expect((await readThread(request, second.thread.id)).runs).toHaveLength(1);
  } catch (error) {
    originalFailure = error;
    throw error;
  } finally {
    release();
    try {
      if (routeStarted) await boundedWait(routeFinished, 5_000, "The released POST route did not finish during cleanup");
    } catch (error) {
      if (!originalFailure) throw error;
      test.info().annotations.push({ type: "cleanup", description: String(error) });
    } finally {
      // Preserve the first UI failure instead of waiting indefinitely for a
      // route after the page or request has already been interrupted.
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  }
});

async function boundedWait<T>(promise: Promise<T>, timeout: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(message)), timeout); }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

test("reference preview animates, traps keyboard focus and restores focus after Escape", async ({ page, request }) => {
  test.setTimeout(60_000);
  const { thread, artifact } = await createDocument(request);
  await setup(page, thread, "en");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await openDocument(page, artifact, "en");
  await page.evaluate(() => {
    const samples: Array<{ className: string; opacity: string; transform: string }> = [];
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        const target = record.target;
        if (target instanceof HTMLElement && target.matches(".composer-artifact-reference, .artifact-reference-dialog")) {
          samples.push({ className: target.className, opacity: target.style.opacity, transform: target.style.transform });
        }
      }
    });
    observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ["style"] });
    Object.assign(window, { __referenceMotionSamples: samples, __referenceMotionObserver: observer });
  });
  await page.getByRole("button", { name: "Use in conversation", exact: true }).click();
  const reference = page.getByTestId("composer-artifact-reference");
  await expect(reference).toBeVisible();
  await expect.poll(() => reference.evaluate((element) => (element as HTMLElement).style.transform)).toBe("");
  const preview = reference.getByRole("button", { name: "View referenced document", exact: true });
  await preview.click();
  const dialog = page.getByRole("dialog", { name: artifact.title, exact: true });
  const close = dialog.getByRole("button", { name: "Close document reference", exact: true });
  await expect(dialog).toBeVisible();
  await expect(close).toBeFocused();
  await expect.poll(() => dialog.evaluate((element) => (element as HTMLElement).style.transform)).toBe("");
  expect(await dialog.locator("pre").textContent()).toBe(artifact.content);
  await page.keyboard.press("Tab");
  await expect(dialog.locator("pre")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.locator("pre")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(preview).toBeFocused();
  const samples = await page.evaluate(() => {
    const state = window as Window & {
      __referenceMotionSamples?: Array<{ className: string; opacity: string; transform: string }>;
      __referenceMotionObserver?: MutationObserver;
    };
    state.__referenceMotionObserver?.disconnect();
    return state.__referenceMotionSamples ?? [];
  });
  for (const className of ["composer-artifact-reference", "artifact-reference-dialog"]) {
    expect(samples.some((sample) => sample.className.includes(className) && sample.opacity !== "" && Number(sample.opacity) < 1 && sample.transform !== "")).toBe(true);
  }
  expect(await reference.evaluate((element) => {
    const { opacity, visibility, transform } = (element as HTMLElement).style;
    return { opacity, visibility, transform };
  })).toEqual({ opacity: "", visibility: "", transform: "" });
});
