import { expect, test, type Page } from "@playwright/test";

async function requestAction(page: Page) {
  await page.addInitScript(() => { if (!localStorage.getItem("alcuin-locale")) localStorage.setItem("alcuin-locale", "en"); });
  await page.goto("/studio?thread=new");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await composer.fill("Update this incident to monitoring");
  await composer.press("Enter");
  const card = page.getByRole("region", { name: "Your confirmation is needed" });
  await expect(card).toBeVisible({ timeout: 15_000 });
  return card;
}

test("reviews the real action, saves a reason, and keeps its receipt across later turns and reload", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  const card = await requestAction(page);
  await expect(card.locator("dt").filter({ hasText: "ticket id" })).toBeVisible();
  await expect(card.locator("dd").filter({ hasText: "INC-104" })).toBeVisible();
  const disclosure = card.getByRole("button", { name: "ops.update_ticket", exact: true });
  await disclosure.click();
  await expect(disclosure).toHaveAttribute("aria-expanded", "false");
  await expect(card.locator(".action-details-motion")).toHaveCSS("opacity", "0");
  await disclosure.press("Enter");
  await expect(disclosure).toHaveAttribute("aria-expanded", "true");
  await card.getByRole("textbox", { name: "Reason (optional)" }).fill("Confirmed the record and scope with its owner.");
  await card.getByRole("button", { name: "Allow once", exact: true }).click();
  const receipt = page.getByRole("region", { name: "Action completed" });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByRole("status")).toBeFocused();
  await expect(receipt).toContainText("Confirmed the record and scope with its owner.");
  await expect(receipt.getByRole("button", { name: "Allow once", exact: true })).toHaveCount(0);
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await expect(composer).toBeEnabled();
  await composer.fill("Find the current record");
  await composer.press("Enter");
  await expect(page.locator(".conversation-pane")).toContainText("I reviewed the current record", { timeout: 15_000 });
  await page.reload();
  await expect(receipt).toContainText("Confirmed the record and scope with its owner.");
  await expect(receipt.locator(".action-details-toggle")).toHaveAttribute("aria-expanded", "false");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await testInfo.attach("approval-receipt", { body: await receipt.screenshot(), contentType: "image/png" });
});

test("a failed submission preserves the note and offers retry; refusal is saved in Chinese dark mode", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await requestAction(page);
  await page.getByRole("button", { name: "Toggle theme", exact: true }).click();
  await page.getByRole("button", { name: "Switch to Chinese", exact: true }).click();
  const translatedCard = page.getByRole("region", { name: "需要你确认" });
  await translatedCard.getByRole("textbox", { name: "原因（可选）" }).fill("记录范围不对，暂不修改。\n先核对来源。");
  let requests = 0;
  await page.route("**/v1/runs/*/approvals/*", async (route) => {
    requests++;
    if (requests === 1) await route.fulfill({ status: 503, json: { detail: "Temporary connection failure" } });
    else await route.continue();
  });
  await translatedCard.getByRole("button", { name: "不允许", exact: true }).click();
  await expect(translatedCard.getByRole("alert")).toContainText("Temporary connection failure");
  await expect(translatedCard.getByRole("textbox")).toHaveValue("记录范围不对，暂不修改。\n先核对来源。");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await translatedCard.getByRole("button", { name: "不允许", exact: true }).click();
  const receipt = page.getByRole("region", { name: "你已拒绝这次操作" });
  await expect(receipt).toContainText("记录范围不对，暂不修改。");
  expect(requests).toBe(2);
  await page.reload();
  await expect(receipt).toContainText("先核对来源。");
  await expect(receipt.getByRole("textbox")).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await testInfo.attach("approval-refused-dark", { body: await receipt.screenshot(), contentType: "image/png" });
});

test("an approved but failed action never shows a completed result", async ({ page }) => {
  await requestAction(page);
  await page.route("**/v1/runs/*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.status === "completed" && body.events?.some((event: { type: string }) => event.type === "approval.decided")) {
      body.status = "failed";
      body.events = body.events.map((event: { type: string; payload: Record<string, unknown> }) => event.type === "tool.completed" ? { ...event, payload: { ...event.payload, status: "failed" } } : event.type === "run.completed" ? { ...event, type: "run.failed", payload: { message: "Operation could not complete" } } : event);
    }
    await route.fulfill({ response, json: body });
  });
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await expect(page.getByRole("region", { name: "Action failed" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Action completed" })).toHaveCount(0);
  await expect(page.getByText("Operation approved and completed", { exact: true })).toHaveCount(0);
});
