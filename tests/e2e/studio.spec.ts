import { expect, test } from "@playwright/test";

test("runs an approval-gated operation through its real adapter", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "Covered by the focused mobile workspace test");
  await page.goto("/studio");
  await expect(page.getByText("Operations Copilot", { exact: true }).first()).toBeVisible();

  const composer = page.getByPlaceholder("Message Operations Copilot…");
  await composer.fill("Update this incident to monitoring");
  await composer.press("Enter");
  await expect(page.getByRole("button", { name: "Approve once" })).toBeVisible();
  await page.getByRole("button", { name: "Approve once" }).click();

  await expect(page.getByText("The approved ops.update_ticket operation completed successfully.")).toBeVisible();
  await expect(page.getByText("Complete", { exact: true })).toBeVisible();
});

test("renders declarative extension blocks and routes forms through approval", async ({ page }, testInfo) => {
  await page.goto("/studio");
  await expect(page.getByText("Operations Copilot", { exact: true }).first()).toBeVisible();

  const composer = page.getByPlaceholder("Message Operations Copilot…");
  await composer.fill("Find the active incident");
  await composer.press("Enter");
  await expect(page.getByText("Complete", { exact: true })).toBeVisible();

  if (testInfo.project.name === "mobile") {
    await page.getByRole("tab", { name: /^Canvas/ }).click();
  }
  await page.getByRole("button", { name: /^Blocks/ }).click();
  await expect(page.getByText("Active record", { exact: true })).toBeVisible();
  await expect(page.getByText("Incident results", { exact: true })).toBeVisible();
  await expect(page.locator('[data-ui-block="ops-toolkit:incident-summary"] tbody')).toContainText("INC-104");
  await expect(page.getByLabel("Incident ID")).toHaveValue("INC-104");

  await page.getByLabel("New status").selectOption("resolved");
  await page.getByRole("button", { name: "Request update" }).click();
  await expect(page.getByRole("button", { name: "Approve once" })).toBeVisible();
  await page.getByRole("button", { name: "Approve once" }).click();

  await expect(page.getByText("The approved ops.update_ticket operation completed successfully.")).toBeVisible();
  await expect(page.getByText("Complete", { exact: true })).toBeVisible();
});

test("keeps the mobile workspace single-panel and supports dark theme", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile-only responsive assertion");
  await page.goto("/studio");
  await expect(page.getByText("Operations Copilot", { exact: true }).first()).toBeVisible();

  await expect(page.locator(".sidebar")).toHaveClass(/sidebar-collapsed/);
  await expect(page.locator(".context-canvas")).toBeHidden();
  await expect(page.locator(".conversation-pane")).toBeVisible();
  await page.getByRole("tab", { name: /^Canvas/ }).click();
  await expect(page.locator(".conversation-pane")).toBeHidden();
  await expect(page.locator(".context-canvas")).toBeVisible();
  await page.getByRole("tab", { name: "Chat" }).click();
  await expect(page.locator(".conversation-pane")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
