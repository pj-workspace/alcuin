import { randomUUID } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const api = "http://localhost:8100";
const headers = { "X-Alcuin-Workspace": "ws_demo" };
const approvedAnswer = "I updated INC-104 to monitoring after your approval. The next step is to review the incident metrics and confirm that the mitigation remains effective before closing the record.";
const deniedAnswer = "I left INC-104 unchanged because you declined the update. As an alternative, review the current incident details and confirm the intended status before requesting another change.";

type Run = {
  id: string;
  thread_id: string;
  status: string;
  events: Array<{ type: string; payload: Record<string, unknown> }>;
};

async function readRun(request: APIRequestContext, id: string): Promise<Run> {
  const response = await request.get(`${api}/v1/runs/${id}`, { headers });
  expect(response.ok()).toBe(true);
  return response.json();
}

async function requestUpdate(page: Page, suffix = "") {
  await page.addInitScript(() => localStorage.setItem("alcuin-locale", "en"));
  await page.goto("/studio?thread=new");
  const created = page.waitForResponse((response) =>
    /\/v1\/threads\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
    && response.request().method() === "POST");
  const composer = page.getByRole("textbox", { name: "Message input", exact: true });
  await composer.fill(`Update this incident to monitoring ${suffix}`);
  await composer.press("Enter");
  const run = await (await created).json() as Run;
  await expect(page.getByRole("region", { name: "Your confirmation is needed" })).toBeVisible({ timeout: 15_000 });
  return run;
}

function finalText(run: Run) {
  return run.events.filter((event) => event.type === "message.delta")
    .map((event) => String(event.payload.delta ?? "")).join("");
}

test("approval returns before model completion and reload resumes the same durable run", async ({ page, request }) => {
  test.setTimeout(60_000);
  const gateId = randomUUID();
  const run = await requestUpdate(page, `[continuation-gate:${gateId}]`);
  const decision = page.waitForResponse((response) =>
    response.url().includes(`/v1/runs/${run.id}/approvals/`)
    && response.request().method() === "POST");
  try {
    await page.getByRole("button", { name: "Allow once", exact: true }).click();
    expect((await decision).ok()).toBe(true);
    // The provider is explicitly blocked after its first delta: accepting the
    // approval must not hold the HTTP response until the model finishes.
    await expect.poll(async () => finalText(await readRun(request, run.id))).toContain("I updated INC-104");
    await expect(page.locator(".conversation-pane")).toContainText("I updated INC-104");
    const partial = await readRun(request, run.id);
    expect(partial.status).toBe("running");
    expect(partial.events.some((event) => event.type === "run.completed")).toBe(false);
    expect(partial.events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "approval.decided", payload: expect.objectContaining({ decision: "approved" }) }),
      expect.objectContaining({ type: "tool.completed", payload: expect.objectContaining({ tool: "ops.update_ticket", status: "succeeded", result: expect.objectContaining({ ticket_id: "INC-104", updated: true }) }) }),
    ]));
    await page.reload();
    await expect(page.getByRole("region", { name: "Action completed" })).toBeVisible();
    await expect(page.locator(".conversation-pane")).toContainText("I updated INC-104");
  } finally {
    const release = await request.post(`http://127.0.0.1:9412/_test/provider-gates/${gateId}/release`);
    expect(release.ok()).toBe(true);
  }
  await expect(page.locator(".conversation-pane")).toContainText(approvedAnswer, { timeout: 15_000 });
  await expect.poll(async () => (await readRun(request, run.id)).status).toBe("completed");
  const complete = await readRun(request, run.id);
  expect(finalText(complete)).toBe(approvedAnswer);
  expect(complete.events.filter((event) => event.type === "run.completed")).toHaveLength(1);
  expect(complete.events.filter((event) => event.type === "tool.completed" && event.payload.tool === "ops.update_ticket")).toHaveLength(1);
  expect(finalText(complete)).not.toContain("The approved ops.update_ticket operation completed successfully.");
  await page.reload();
  await expect(page.locator(".conversation-pane")).toContainText(approvedAnswer);
  await expect(page.getByRole("region", { name: "Action completed" })).toBeVisible();
  const threadResponse = await request.get(`${api}/v1/threads/${run.thread_id}`, { headers });
  expect(threadResponse.ok()).toBe(true);
  const thread = await threadResponse.json() as { runs: Run[] };
  expect(thread.runs.map((item) => item.id)).toEqual([run.id]);
});

test("denial resumes the model with an alternative and never records a successful mutation", async ({ page, request }) => {
  const run = await requestUpdate(page);
  await page.getByRole("button", { name: "Do not allow", exact: true }).click();
  await expect(page.getByRole("region", { name: "You declined this action" })).toBeVisible();
  await expect(page.locator(".conversation-pane")).toContainText(deniedAnswer, { timeout: 15_000 });
  await expect.poll(async () => (await readRun(request, run.id)).status).toBe("completed");
  const complete = await readRun(request, run.id);
  expect(finalText(complete)).toBe(deniedAnswer);
  const actionResults = complete.events.filter((event) => event.type === "tool.completed" && event.payload.tool === "ops.update_ticket");
  expect(actionResults).toHaveLength(1);
  expect(actionResults[0].payload.status).toBe("denied");
  expect(actionResults[0].payload.result).toBeUndefined();
  expect(complete.events.filter((event) => event.type === "approval.required")).toHaveLength(1);
  await expect(page.getByRole("region", { name: "Action completed" })).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".conversation-pane")).toContainText(deniedAnswer);
  await expect(page.getByRole("region", { name: "You declined this action" })).toBeVisible();
});
