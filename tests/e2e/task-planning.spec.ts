import { expect, test, type Page } from "@playwright/test";
import type { Task, TaskCreate, TaskPlanProposal } from "../../packages/contracts/src/platform";
import type { ArtifactResource, Thread } from "../../packages/contracts/src/index";

const proposalUrl = "**/v1/threads/*/task-plan-proposals";
const proposal: TaskPlanProposal = {
  goal: "Prepare a clear project brief",
  model: "deepseek-v4-flash-vision-exp",
  reasoning_effort: "medium",
  steps: [
    { title: "Gather context", description: "Identify the goal and constraints." },
    { title: "Write the brief", description: "Produce a concise draft." },
    { title: "Optional appendix", description: "Add supporting details." },
  ],
};

async function openPlanning(page: Page) {
  await page.addInitScript(() => localStorage.setItem("alcuin-locale", "en"));
  await page.goto("/studio?thread=new");
  await expect(page.getByRole("textbox", { name: "Message input", exact: true })).toBeVisible();
  await page.getByRole("group", { name: "Work mode" }).getByRole("button", { name: "Plan", exact: true }).click();
  await page.getByRole("textbox", { name: "Message input", exact: true }).fill(proposal.goal);
}

// Keep bootstrap, Agent configuration, and Thread creation real. Only the
// proposal and Task lifecycle are deterministic here; these tests do not prove
// provider quality or backend Task execution.
async function mockTaskLifecycle(page: Page, options: { failFirstStart?: boolean; onStart?: (task: Task) => Task } = {}) {
  const creates: TaskCreate[] = [];
  const commands: string[] = [];
  let task: Task | null = null;
  const taskId = "tsk_e2e_plan_preview";
  await page.route("**/v1/threads/*/tasks", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON() as TaskCreate;
    creates.push(body);
    const now = new Date().toISOString();
    task = {
      id: taskId, workspace_id: "ws_demo",
      thread_id: new URL(route.request().url()).pathname.split("/").at(-2)!,
      goal: body.goal, model_override: body.model_override, reasoning_effort: body.reasoning_effort,
      status: "ready", revision: 1, created_at: now, updated_at: now,
      plan: { id: "plan_e2e_preview", task_id: taskId, updated_at: now, steps: (body.steps ?? []).map((step, ordinal) => ({
        ...step, description: step.description ?? "", ordinal, id: `step_${ordinal}`, task_id: taskId,
        status: "pending", attempts: [], evidence: [],
      })) },
    };
    await route.fulfill({ json: task });
  });
  await page.route(`**/v1/tasks/${taskId}/**`, async (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/commands")) {
      const { command } = route.request().postDataJSON() as { command: string };
      commands.push(command);
      if (!task) throw new Error("Task command arrived before creation");
      if (options.failFirstStart && command === "start" && commands.filter((item) => item === "start").length === 1) {
        await route.fulfill({ status: 503, json: { detail: "Task created, but start temporarily unavailable" } });
        return;
      }
      task = { ...task, revision: task.revision + 1, status: command === "pause" ? "paused" : command === "cancel" ? "cancelled" : "running" };
      if (command === "start" && options.onStart) task = options.onStart(task);
      await route.fulfill({ json: task });
    } else {
      await route.fulfill({ contentType: "text/event-stream", body: ": fixture stream complete\n\n" });
    }
  });
  await page.route(`**/v1/tasks/${taskId}`, (route) => route.fulfill({ json: task }));
  return { creates, commands };
}

test("reviews and edits a proposal before creating a Task, then exposes working controls", async ({ page }) => {
  const lifecycle = await mockTaskLifecycle(page);
  await page.route(proposalUrl, (route) => route.fulfill({ json: proposal }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await expect(preview.getByRole("button", { name: "Confirm & run" })).toBeVisible();
  expect(lifecycle.creates).toHaveLength(0);
  expect(lifecycle.commands).toHaveLength(0);

  await preview.getByRole("textbox", { name: "Step title 1", exact: true }).fill("Collect verified context");
  await preview.getByRole("textbox", { name: "What this step should accomplish 1", exact: true }).fill("Read the provided project notes.");
  await preview.getByRole("button", { name: "Move up: Write the brief", exact: true }).click();
  await preview.getByRole("button", { name: "Remove step: Optional appendix", exact: true }).click();
  await expect(preview.getByRole("textbox", { name: /^Step title/ })).toHaveCount(2);
  await expect(preview.getByRole("textbox", { name: "Step title 1", exact: true })).toHaveValue("Write the brief");
  expect(lifecycle.creates).toHaveLength(0);
  await preview.getByRole("button", { name: "Confirm & run" }).click();
  await expect(preview).toBeHidden();
  expect(lifecycle.creates).toEqual([{
    goal: proposal.goal, model_override: proposal.model, reasoning_effort: "medium",
    steps: [proposal.steps[1], { title: "Collect verified context", description: "Read the provided project notes." }],
  }]);
  await expect.poll(() => lifecycle.commands).toEqual(["start"]);
  const controls = page.locator(".task-canvas");
  await controls.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(controls.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  await controls.getByRole("button", { name: "Resume", exact: true }).click();
  await controls.getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(() => lifecycle.commands).toEqual(["start", "pause", "resume", "cancel"]);
});

test("cancels a pending proposal and ignores its late response", async ({ page }) => {
  const lifecycle = await mockTaskLifecycle(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let arrived = false;
  let delivered = false;
  await page.route(proposalUrl, async (route) => {
    arrived = true;
    await gate;
    // The browser may already have aborted its fetch after cancellation.
    await route.fulfill({ json: proposal }).catch(() => undefined);
    delivered = true;
  });
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  await expect.poll(() => arrived).toBe(true);
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await preview.getByRole("button", { name: "Cancel generation" }).click();
  release();
  await expect.poll(() => delivered).toBe(true);
  await expect(preview).toBeHidden();
  expect(lifecycle.creates).toHaveLength(0);
  if (await page.getByRole("tab", { name: "Chat", exact: true }).isVisible()) await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message input", exact: true })).toHaveValue(proposal.goal);
});

test("Task artifact evidence refreshes the artifact list without navigation or replacing the Task panel", async ({ page }) => {
  let artifact: ArtifactResource | null = null;
  let refreshedAfterTask = false;
  await page.route("**/v1/threads/*/artifacts?*", async (route) => {
    if (artifact) refreshedAfterTask = true;
    await route.fulfill({ json: artifact ? [artifact] : [] });
  });
  await mockTaskLifecycle(page, { onStart: (task) => {
    artifact = {
      id: "art_task_e2e", workspace_id: task.workspace_id, thread_id: task.thread_id,
      source_run_id: "run_task_e2e", title: "Completed project brief", kind: "document",
      content_type: "text/markdown", content: "The canonical task document is available without reloading.",
      version: 1, created_at: task.created_at, updated_at: task.updated_at,
    };
    return {
      ...task, status: "completed",
      plan: { ...task.plan, steps: task.plan.steps.map((step) => ({
        ...step, status: "completed", evidence: [{
          id: `evidence:${step.id}`, task_id: task.id, step_id: step.id, kind: "artifact",
          label: artifact!.title, summary: "Task output summary only", resource_id: artifact!.id, created_at: task.created_at,
        }],
      })) },
    };
  } });
  await page.route(proposalUrl, (route) => route.fulfill({ json: proposal }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  await page.getByRole("button", { name: "Confirm & run", exact: true }).click();
  await expect.poll(() => refreshedAfterTask).toBe(true);
  await expect(page.locator(".task-canvas")).toBeVisible();
  await page.locator("#canvas-tab-artifact").click();
  await expect(page.locator(".artifact-document")).toContainText("The canonical task document is available without reloading.");
  await expect(page.locator(".artifact-document")).not.toContainText("Task output summary only");
});

test("changing threads during generation discards the old proposal and unlocks planning", async ({ page }, testInfo) => {
  const lifecycle = await mockTaskLifecycle(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let requested = 0;
  let delivered = false;
  await page.route(proposalUrl, async (route) => {
    requested += 1;
    if (requested === 1) {
      await gate;
      await route.fulfill({ json: proposal }).catch(() => undefined);
      delivered = true;
    } else {
      await route.fulfill({ json: { ...proposal, goal: "Plan in the new thread" } });
    }
  });
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  await expect.poll(() => requested).toBe(1);
  if (testInfo.project.name === "mobile") await page.getByRole("button", { name: "Toggle sidebar", exact: true }).click();
  await page.locator(".sidebar .new-thread-row").click();
  await expect(page).toHaveURL(/thread=new/);
  release();
  await expect.poll(() => delivered).toBe(true);
  await expect(page.getByRole("region", { name: "Task plan preview" })).toBeHidden();
  expect(lifecycle.creates).toHaveLength(0);
  const planMode = page.getByRole("group", { name: "Work mode" }).getByRole("button", { name: "Plan", exact: true });
  await expect(planMode).toBeEnabled();
  await planMode.click();
  await page.getByRole("textbox", { name: "Message input", exact: true }).fill("Plan in the new thread");
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await expect(preview.getByRole("button", { name: "Confirm & run" })).toBeVisible();
  await expect(preview.getByRole("textbox", { name: "Task", exact: true })).toHaveValue("Plan in the new thread");
  expect(requested).toBe(2);
  expect(lifecycle.creates).toHaveLength(0);
});

test("a failed start retains the created Task and retries without creating a duplicate", async ({ page }) => {
  const lifecycle = await mockTaskLifecycle(page, { failFirstStart: true });
  await page.route(proposalUrl, (route) => route.fulfill({ json: proposal }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await preview.getByRole("button", { name: "Confirm & run" }).click();
  await expect(preview).toBeHidden();
  const taskCanvas = page.locator(".task-canvas");
  await expect(taskCanvas.getByRole("button", { name: "Start", exact: true })).toBeEnabled();
  await expect.poll(() => lifecycle.commands).toEqual(["start"]);
  expect(lifecycle.creates).toHaveLength(1);
  await taskCanvas.getByRole("button", { name: "Start", exact: true }).click();
  await expect(taskCanvas.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await expect.poll(() => lifecycle.commands).toEqual(["start", "start"]);
  expect(lifecycle.creates).toHaveLength(1);
});

test("allows a manual plan after a proposal error without running an empty step", async ({ page }) => {
  const lifecycle = await mockTaskLifecycle(page);
  await page.route(proposalUrl, (route) => route.fulfill({ status: 503, json: { detail: "Planning provider temporarily unavailable" } }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await preview.getByRole("button", { name: "Write a plan" }).click();
  await expect(preview.getByRole("button", { name: "Confirm & run" })).toBeDisabled();
  await preview.getByRole("textbox", { name: "Step title 1", exact: true }).fill("Summarize the supplied notes");
  await expect(preview.getByRole("button", { name: "Confirm & run" })).toBeEnabled();
  expect(lifecycle.creates).toHaveLength(0);
  await preview.getByRole("button", { name: "Confirm & run" }).click();
  await expect.poll(() => lifecycle.creates.length).toBe(1);
  expect(lifecycle.creates[0].steps).toEqual([{ title: "Summarize the supplied notes", description: "" }]);
});

test("IME Enter keeps editing while ordinary Enter requests a plan", async ({ page }) => {
  let proposals = 0;
  await page.route(proposalUrl, (route) => { proposals += 1; return route.fulfill({ json: proposal }); });
  await openPlanning(page);
  const input = page.getByRole("textbox", { name: "Message input", exact: true });
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, keyCode: 229 });
  await expect(page.getByRole("region", { name: "Task plan preview" })).toBeHidden();
  await expect(input).toHaveValue(proposal.goal);
  expect(proposals).toBe(0);
  await input.press("Enter");
  await expect(page.getByRole("region", { name: "Task plan preview" }).getByRole("button", { name: "Confirm & run" })).toBeVisible();
  expect(proposals).toBe(1);
});

test("confirming a Task wakes naming after an empty Thread stopped polling", async ({ page }) => {
  const lifecycle = await mockTaskLifecycle(page);
  let thread: Thread | undefined;
  let pendingReads = 0;
  await page.route("**/v1/threads", async (route) => {
    const response = await route.fetch();
    thread = await response.json() as Thread;
    await route.fulfill({ response, json: { ...thread, title: "New agent thread", title_status: "pending" } });
  });
  await page.route(/\/v1\/threads\/[^/]+\/title(?:\/ensure)?$/, async (route) => {
    if (!lifecycle.creates.length) pendingReads += 1;
    await route.fulfill({ json: { ...thread, title: lifecycle.creates.length ? "Project brief execution" : "New agent thread", title_status: lifecycle.creates.length ? "ready" : "pending", updated_at: new Date().toISOString() } });
  });
  await page.route(proposalUrl, (route) => route.fulfill({ json: proposal }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await expect(preview.getByRole("button", { name: "Confirm & run" })).toBeVisible();
  await expect.poll(() => pendingReads).toBeGreaterThanOrEqual(4);
  await preview.getByRole("button", { name: "Confirm & run" }).click();
  await expect(preview).toBeHidden();
  if (await page.locator(".sidebar").evaluate((element) => element.classList.contains("sidebar-collapsed"))) await page.getByRole("button", { name: "Toggle sidebar", exact: true }).click();
  await expect(page.locator(".thread-title").filter({ hasText: "Project brief execution" })).toBeVisible();
  expect(lifecycle.creates).toHaveLength(1);
});

test("mobile dark planning remains editable with reduced motion", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Focused mobile acceptance");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route(proposalUrl, (route) => route.fulfill({ json: proposal }));
  await openPlanning(page);
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Generate task plan", exact: true }).click();
  const preview = page.getByRole("region", { name: "Task plan preview" });
  await expect(preview.getByRole("textbox", { name: "Step title 1", exact: true })).toBeVisible();
  await preview.getByRole("textbox", { name: "Step title 1", exact: true }).fill("Mobile-edited step");
  await expect(preview.getByRole("textbox", { name: "Step title 1", exact: true })).toHaveValue("Mobile-edited step");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect(await preview.evaluate((element) => getComputedStyle(element).transform)).toBe("none");
  await preview.getByRole("button", { name: "Close plan" }).click();
  await expect(preview).toBeHidden();
});
