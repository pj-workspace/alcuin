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

test("keeps the mobile workspace single-panel and supports dark theme", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile-only responsive assertion");
  await page.goto("/studio");
  await expect(page.getByText("Operations Copilot", { exact: true }).first()).toBeVisible();

  await expect(page.locator(".sidebar")).toHaveClass(/sidebar-collapsed/);
  await expect(page.locator(".context-canvas")).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
