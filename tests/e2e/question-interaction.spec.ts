import { expect, test } from "@playwright/test";

test("question survives reload; a chosen and edited answer resumes the same Run", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => localStorage.setItem("alcuin-locale", "en"));
  await page.goto("/studio?thread=new");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await composer.fill("Ask my audience before preparing a report");
  await composer.press("Enter");
  const question = page.getByRole("region", { name: "A question for you" });
  await expect(question).toBeVisible({ timeout: 15_000 });
  // Waiting for the user is not ongoing tool execution. Expanding the trace
  // must show the pending step without an indefinitely spinning loader.
  const trace = page.locator(".brainstorm-toggle").last();
  await expect(trace).toContainText("Waiting for your answer");
  await expect(trace).toHaveAttribute("aria-expanded", "false");
  await trace.click();
  await expect(page.locator(".brainstorm-collapse").last()).not.toHaveAttribute("inert");
  await expect(page.locator(".brainstorm-node.tool .micro-loader")).toHaveCount(0);
  await expect(page.locator(".tool-step-label").last()).toContainText("Waiting for your answer");
  await expect(page.getByRole("button", { name: "Answer the question above or choose to skip", exact: true })).toBeDisabled();
  await page.reload();
  await expect(question).toBeVisible();
  await question.getByRole("radio", { name: "Management" }).check();
  const answer = question.getByRole("textbox", { name: "Your answer or additional details" });
  await expect(answer).toHaveValue("Management");
  await answer.fill("Management. Keep the report concise.");
  await testInfo.attach("question-ready", { body: await question.screenshot(), contentType: "image/png" });
  let submits = 0;
  await page.route("**/v1/runs/*/inputs/*", async (route) => {
    submits++;
    if (submits === 1) await route.fulfill({ status: 503, json: { detail: "Temporary connection failure" } });
    else await route.continue();
  });
  await question.getByRole("button", { name: "Answer and continue", exact: true }).click();
  await expect(question.getByRole("alert")).toContainText("Temporary connection failure");
  await expect(answer).toHaveValue("Management. Keep the report concise.");
  await question.getByRole("button", { name: "Answer and continue", exact: true }).click();
  const receipt = page.getByRole("region", { name: "Your answer", exact: true });
  await expect(receipt).toContainText("Management. Keep the report concise.");
  await expect(receipt.getByRole("heading")).toBeFocused();
  await expect(composer).toBeEnabled();
  const threadId = new URL(page.url()).searchParams.get("thread");
  const detail = await page.request.get(`http://localhost:8100/v1/threads/${threadId}`, { headers: { "X-Alcuin-Workspace": "ws_demo" } });
  const snapshot = await detail.json();
  expect(snapshot.runs).toHaveLength(1);
  expect(snapshot.runs[0].status).toBe("completed");
  expect(snapshot.input_events.map((event: { type: string }) => event.type)).toEqual(["input.required", "input.answered"]);
  await page.reload();
  await expect(receipt).toContainText("Management. Keep the report concise.");
  await expect(receipt.getByRole("textbox")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("explicit skip remains visible in Chinese dark mode with reduced motion", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => { localStorage.setItem("alcuin-locale", "zh"); localStorage.setItem("alcuin-theme", "dark"); });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/studio?thread=new");
  const composer = page.getByRole("textbox", { name: "消息输入", exact: true });
  await composer.fill("Ask my audience before preparing a report");
  await composer.press("Enter");
  const question = page.getByRole("region", { name: "需要你补充一点信息" });
  await expect(question).toBeVisible({ timeout: 15_000 });
  await expect(question.getByRole("button", { name: "回答并继续", exact: true })).toBeDisabled();
  await question.getByRole("button", { name: "跳过，由 AI 继续", exact: true }).click();
  const receipt = page.getByRole("region", { name: "已跳过，由 AI 继续", exact: true });
  await expect(receipt).toBeVisible();
  await expect(composer).toBeEnabled();
  await page.reload();
  await expect(receipt).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach("question-skipped-dark", { body: await receipt.screenshot(), contentType: "image/png" });
});
