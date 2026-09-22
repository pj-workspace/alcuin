import { expect, test } from "@playwright/test";

test("mobile composer keeps one toolbar row and edits the actual run profile in a named panel", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile interaction acceptance");
  test.setTimeout(60_000);
  await page.addInitScript(() => localStorage.setItem("alcuin-locale", "en"));
  await page.goto("/studio?thread=new");
  const input = page.getByRole("textbox", { name: "Message input", exact: true });
  await expect(input).toBeVisible();
  const settings = page.getByRole("button", { name: "Conversation settings", exact: true });
  const send = page.locator(".send-button");
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(settings).toBeVisible();
    const boxes = await Promise.all([page.getByRole("button", { name: "Attach files", exact: true }), settings, send].map((item) => item.boundingBox()));
    expect(Math.max(...boxes.map((box) => box!.y)) - Math.min(...boxes.map((box) => box!.y))).toBeLessThanOrEqual(2);
    expect(boxes.every((box) => box!.height >= 44)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator(".composer-hint")).toBeHidden();
    await expect(page.locator(".composer .run-profile-controls")).toBeHidden();
  }
  await settings.click();
  const panel = page.getByRole("dialog", { name: "Conversation settings", exact: true });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("combobox", { name: "Model profile", exact: true })).toBeFocused();
  await panel.getByRole("combobox", { name: "Model profile", exact: true }).selectOption({ label: "Pro" });
  await panel.getByRole("combobox", { name: "Thinking effort", exact: true }).selectOption({ label: "High" });
  await testInfo.attach("mobile-composer-settings", { body: await page.screenshot(), contentType: "image/png" });
  await panel.getByRole("combobox", { name: "Thinking effort", exact: true }).press("Escape");
  await expect(panel).toBeHidden();
  await expect(settings).toBeFocused();
  await input.fill("Find the current record");
  const accepted = page.waitForRequest((request) => request.method() === "POST" && /\/threads\/[^/]+\/runs$/.test(request.url()));
  await send.click();
  const payload = (await accepted).postDataJSON();
  expect(payload.model_override).toBe("deepseek-v4-pro");
  expect(payload.reasoning_effort).toBe("high");
  await expect(page.locator(".assistant-output")).toContainText("I reviewed the current record", { timeout: 15_000 });
  await testInfo.attach("mobile-composer-compact", { body: await page.screenshot(), contentType: "image/png" });
});

test("mobile settings close on outside touch and desktop resize, with Chinese dark and reduced motion", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile interaction acceptance");
  await page.addInitScript(() => { localStorage.setItem("alcuin-locale", "zh"); localStorage.setItem("alcuin-theme", "dark"); });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/studio?thread=new");
  const settings = page.getByRole("button", { name: "对话设置", exact: true });
  await settings.click();
  const panel = page.getByRole("dialog", { name: "对话设置", exact: true });
  await expect(panel).toBeVisible();
  await page.getByRole("textbox", { name: "消息输入", exact: true }).click();
  await expect(panel).toBeHidden();
  await settings.click();
  await page.setViewportSize({ width: 320, height: 568 });
  const box = await panel.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(320);
  await page.setViewportSize({ width: 1100, height: 844 });
  await expect(panel).toBeHidden();
  await expect(page.locator(".run-profile-controls")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(settings).toHaveAttribute("aria-expanded", "false");
  await expect(panel).toBeHidden();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
