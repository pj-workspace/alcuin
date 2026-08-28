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
  await expect(page.locator('[data-ui-block="ops-toolkit:incident-summary"] tbody')).toContainText("REC-104");
  await expect(page.getByLabel("Incident ID")).toHaveValue("REC-104");

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

test("switches the full workspace between English and Chinese and persists the locale", async ({ page }) => {
  await page.goto("/studio");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  await page.getByRole("button", { name: "Switch to Chinese" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByPlaceholder("给 Operations Copilot 发消息…")).toBeVisible();

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByPlaceholder("给 Operations Copilot 发消息…")).toBeVisible();

  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "智能体构建器", exact: true })).toBeVisible();
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "运行记录", exact: true })).toBeVisible();
  await page.goto("/embed");
  await expect(page.getByRole("heading", { name: "嵌入式演练场", exact: true })).toBeVisible();
  await page.locator(".header-actions").getByRole("button", { name: "创建会话" }).click();
  const embeddedAgent = page.locator("alcuin-agent");
  await expect(embeddedAgent).toHaveAttribute("lang", "zh-CN");
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("有什么可以帮你？");
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("已连接");
  await page.goto("/extensions");
  await expect(page.getByRole("heading", { name: "扩展", exact: true })).toBeVisible();
  await expect(page.getByText("能力注册中心", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "切换到英文" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Extensions", exact: true })).toBeVisible();
});

test("runs the real Embed component through origin-scoped read and approval flows", async ({ page }) => {
  await page.goto("/embed");
  await page.locator(".header-actions").getByRole("button", { name: "Create session" }).click();
  const embeddedAgent = page.locator("alcuin-agent");
  await expect(embeddedAgent).toHaveAttribute("lang", "en");

  await embeddedAgent.evaluate((element) => {
    const eventWindow = window as unknown as { __alcuinHostEvents: string[] };
    eventWindow.__alcuinHostEvents = [];
    for (const eventName of [
      "alcuin:run-start",
      "alcuin:event",
      "alcuin:artifact",
      "alcuin:approval",
      "alcuin:error",
    ]) {
      element.addEventListener(eventName, () => eventWindow.__alcuinHostEvents.push(eventName));
    }
    const input = element.shadowRoot?.querySelector("textarea");
    if (!(input instanceof HTMLTextAreaElement)) throw new Error("Embed composer is unavailable");
    input.value = "Summarize the active checkout incident";
    input.form?.requestSubmit();
  });
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("I reviewed the current record for INC-104");
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { __alcuinHostEvents?: string[] }
  ).__alcuinHostEvents ?? [])).toEqual(expect.arrayContaining([
    "alcuin:run-start",
    "alcuin:event",
    "alcuin:artifact",
  ]));
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { __alcuinHostEvents?: string[] }
  ).__alcuinHostEvents ?? [])).not.toContain("alcuin:error");

  await embeddedAgent.evaluate((element) => {
    const input = element.shadowRoot?.querySelector("textarea");
    if (!(input instanceof HTMLTextAreaElement)) throw new Error("Embed composer is unavailable");
    input.value = "Update this incident to monitoring";
    input.form?.requestSubmit();
  });
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("Approve tool execution");
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { __alcuinHostEvents?: string[] }
  ).__alcuinHostEvents ?? [])).toContain("alcuin:approval");
  await embeddedAgent.evaluate((element) => {
    const approve = [...(element.shadowRoot?.querySelectorAll(".approval-actions button") ?? [])]
      .find((button) => button.textContent === "Approve once");
    if (!(approve instanceof HTMLButtonElement)) throw new Error("Embed approval action is unavailable");
    approve.click();
  });
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("Approved and executed");
  await expect.poll(() => page.evaluate(() => (
    window as unknown as { __alcuinHostEvents?: string[] }
  ).__alcuinHostEvents ?? [])).not.toContain("alcuin:error");
});

test("installs and enables real MCP and OpenAPI extensions through the governed lifecycle", async ({ page }, testInfo) => {
  const suffix = testInfo.project.name;
  const mcpName = `E2E Echo MCP ${suffix}`;
  const openApiName = `E2E Records API ${suffix}`;

  await page.goto("/extensions");
  await page.getByRole("button", { name: "Connect capability", exact: true }).click();
  let wizard = page.locator(".extension-wizard");
  await wizard.getByLabel("Name", { exact: true }).fill(mcpName);
  await wizard.getByLabel("Extension ID", { exact: true }).fill(`e2e.${suffix}.mcp`);
  await wizard.getByLabel("Description", { exact: true }).fill("Live stdio lifecycle verification");
  await wizard.getByLabel("Command", { exact: true }).fill("uv");
  await wizard.locator("textarea.code-editor").fill(JSON.stringify([
    "run",
    "--project",
    "apps/api",
    "python",
    "apps/api/tests/fixtures/echo_mcp.py",
  ]));
  await wizard.getByRole("button", { name: "Inspect connection" }).click();
  await expect(wizard.getByText("Contract is valid", { exact: true })).toBeVisible();
  await expect(wizard.locator(".tool-selection").filter({ hasText: "echo" })).toBeVisible();
  const mutatingMcpTool = wizard.locator(".tool-selection").filter({ hasText: "write_marker" });
  await expect(mutatingMcpTool).toContainText("write · approval");
  await mutatingMcpTool.getByRole("checkbox").uncheck();
  await wizard.getByRole("button", { name: "Review selected permissions" }).click();
  await expect(wizard.locator(".tool-selection").filter({ hasText: "write_marker" })).toHaveCount(0);
  await wizard.getByRole("button", { name: "Install disabled" }).click();
  await expect(wizard.getByText("disabled", { exact: true })).toBeVisible();
  await wizard.getByRole("button", { name: "Run health check" }).click();
  await expect(wizard.getByText("healthy", { exact: true }).first()).toBeVisible();
  await wizard.getByRole("button", { name: "Enable extension" }).click();
  await expect(wizard.getByText("enabled", { exact: true })).toBeVisible();
  await wizard.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("heading", { name: mcpName, exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Connect capability", exact: true }).click();
  wizard = page.locator(".extension-wizard");
  await wizard.getByRole("button", { name: "OpenAPI", exact: true }).click();
  await wizard.getByLabel("Name", { exact: true }).fill(openApiName);
  await wizard.getByLabel("Extension ID", { exact: true }).fill(`e2e.${suffix}.records`);
  await wizard.getByRole("button", { name: "Specification URL" }).click();
  await wizard.getByLabel(/^Base URL override/).fill("http://127.0.0.1:9412");
  await wizard.getByLabel("OpenAPI URL", { exact: true }).fill("http://127.0.0.1:9412/openapi.json");
  await wizard.getByRole("button", { name: "Inspect connection" }).click();
  await expect(wizard.getByText("Contract is valid", { exact: true })).toBeVisible();
  await expect(wizard.locator(".tool-selection").filter({ hasText: "getRecord" })).toContainText("read only");
  const mutatingOpenApiTool = wizard.locator(".tool-selection").filter({ hasText: "updateRecord" });
  await expect(mutatingOpenApiTool).toContainText("write · approval");
  const healthOperation = wizard.locator(".tool-selection").filter({ hasText: "healthCheck" });
  await healthOperation.getByRole("checkbox").uncheck();
  await wizard.getByRole("button", { name: "Review selected permissions" }).click();
  await expect(wizard.locator(".tool-selection").filter({ hasText: "healthCheck" })).toHaveCount(0);
  await wizard.getByRole("button", { name: "Install disabled" }).click();
  await expect(wizard.getByText("disabled", { exact: true })).toBeVisible();
  await wizard.getByRole("button", { name: "Run health check" }).click();
  await expect(wizard.getByText("healthy", { exact: true }).first()).toBeVisible();
  await wizard.getByRole("button", { name: "Enable extension" }).click();
  await expect(wizard.getByText("enabled", { exact: true })).toBeVisible();
  await wizard.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("heading", { name: openApiName, exact: true })).toBeVisible();
});

test("creates, edits, publishes, persists, and switches between Agents", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const suffix = testInfo.project.name;
  const draftName = `Release Copilot ${suffix}`;
  const publishedName = `${draftName} v2`;
  const slug = `release-copilot-${suffix}`;
  const sourceName = `Release handbook ${suffix}`;

  await page.goto("/agents");
  await page.locator(".wide-header").getByRole("button", { name: "New agent" }).click();
  const dialog = page.getByRole("dialog", { name: "Create agent" });
  await dialog.getByLabel("Agent name", { exact: true }).fill(draftName);
  await expect(dialog.getByRole("textbox", { name: /^Agent slug/ })).toHaveValue(slug);
  await dialog.getByLabel("Description", { exact: true }).fill("Coordinates governed release readiness.");
  await dialog.getByRole("button", { name: "Create draft" }).click();

  await expect(dialog).toBeHidden();
  await expect(page.getByRole("textbox", { name: /^Agent name/ })).toHaveValue(draftName);
  await expect(page.locator(".title-row .status-pill")).toHaveText("draft");
  await expect(page.locator(".title-row .version-badge")).toHaveText("v1");

  await page.getByRole("textbox", { name: /^Agent name/ }).fill(publishedName);
  await page.locator(".builder-nav").getByRole("button").filter({ hasText: "Capabilities" }).click();
  const webSearch = page.locator(".capability-option").filter({ hasText: "web.search" });
  const incidentSearch = page.locator(".capability-option").filter({ hasText: "ops.search_incidents" });
  await expect(webSearch).toContainText("Bind");
  await webSearch.click();
  await incidentSearch.click();
  await expect(webSearch).toHaveAttribute("aria-pressed", "true");
  await expect(incidentSearch).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Knowledge", exact: true }).click();
  await page.getByRole("button", { name: "Import document" }).click();
  await page.getByRole("button", { name: "Paste text" }).click();
  await page.getByLabel("Source name", { exact: true }).fill(sourceName);
  await page.getByLabel("Document title", { exact: true }).fill("Release readiness policy");
  await page.getByLabel("Content", { exact: true }).fill(
    "Release readiness requires a rollback owner, verified monitoring, and an approval record.",
  );
  await page.getByRole("button", { name: "Index and bind" }).click();
  const knowledgeSource = page.locator(".knowledge-source-row").filter({ hasText: sourceName });
  await expect(knowledgeSource).toContainText("Bound");
  await page.getByRole("button", { name: /^Capabilities/ }).click();
  const knowledgeSearch = page.locator(".capability-option").filter({ hasText: "knowledge.search" });
  await expect(knowledgeSearch).toHaveAttribute("aria-pressed", "true");
  await expect(knowledgeSearch).toContainText("Managed in Knowledge");

  const publishResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/publish") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Publish version" }).click();
  const publishedAgent = await (await publishResponsePromise).json() as { current_version_id: string };
  await expect(page.locator(".title-row .status-pill")).toHaveText("published");
  await expect(page.locator(".title-row .version-badge")).toHaveText("v2");
  await page.getByRole("button", { name: "Identity", exact: true }).click();
  await expect(page.getByRole("textbox", { name: /^Agent name/ })).toHaveValue(publishedName);

  await page.reload();
  await expect(page.getByRole("textbox", { name: /^Agent name/ })).toHaveValue(publishedName);
  await expect(page.locator(".title-row .status-pill")).toHaveText("published");

  await page.goto("/studio");
  await expect(page.getByRole("heading", { name: publishedName, exact: true })).toBeVisible();
  const composer = page.getByPlaceholder(`Message ${publishedName}…`);
  await composer.fill("Find the active incident");
  await composer.press("Enter");
  await expect(page.getByText("Complete", { exact: true })).toBeVisible();
  await expect(page.locator(".conversation-pane").getByText("I reviewed the current record for INC-104.")).toBeVisible();

  const webRunResponsePromise = page.waitForResponse((response) =>
    /\/v1\/threads\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
    && response.request().method() === "POST");
  await composer.fill("Search the public web for the Alcuin agent platform");
  await composer.press("Enter");
  const webRun = await (await webRunResponsePromise).json() as { id: string };
  await expect(page.locator(".conversation-pane").getByText("Web evidence confirms that Alcuin")).toBeVisible();
  const webSnapshot = await page.evaluate(async (runId) => {
    const response = await fetch(`http://localhost:8100/v1/runs/${runId}`, {
      headers: { "X-Alcuin-Workspace": "ws_demo" },
    });
    return response.json();
  }, webRun.id) as { events: Array<{ type: string; payload: Record<string, unknown> }> };
  expect(webSnapshot.events).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: "tool.completed", payload: expect.objectContaining({ tool: "web.search", status: "succeeded" }) }),
    expect.objectContaining({ type: "citation.created", payload: expect.objectContaining({ locator: "https://example.com/alcuin-agent-platform" }) }),
  ]));

  const knowledgeRunResponsePromise = page.waitForResponse((response) =>
    /\/v1\/threads\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
    && response.request().method() === "POST");
  await composer.fill("Use the knowledge handbook to explain the release readiness policy");
  await composer.press("Enter");
  const knowledgeRun = await (await knowledgeRunResponsePromise).json() as { id: string };
  await expect(page.locator(".conversation-pane").getByText("The bound release policy requires")).toBeVisible();
  const knowledgeSnapshot = await page.evaluate(async (runId) => {
    const response = await fetch(`http://localhost:8100/v1/runs/${runId}`, {
      headers: { "X-Alcuin-Workspace": "ws_demo" },
    });
    return response.json();
  }, knowledgeRun.id) as { events: Array<{ type: string; payload: Record<string, unknown> }> };
  expect(knowledgeSnapshot.events).toEqual(expect.arrayContaining([
    expect.objectContaining({ type: "tool.completed", payload: expect.objectContaining({ tool: "knowledge.search", status: "succeeded" }) }),
  ]));
  expect(knowledgeSnapshot.events.some((event) =>
    event.type === "citation.created" && String(event.payload.locator).startsWith("knowledge://"))).toBe(true);

  await page.goto("/embed");
  const sessionResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/v1/embed/sessions") && response.request().method() === "POST");
  await page.locator(".header-actions").getByRole("button", { name: "Create session" }).click();
  const session = await (await sessionResponsePromise).json() as { agent_version_id: string };
  expect(session.agent_version_id).toBe(publishedAgent.current_version_id);
  const embeddedAgent = page.locator("alcuin-agent");
  await embeddedAgent.evaluate((element) => {
    const input = element.shadowRoot?.querySelector("textarea");
    if (!(input instanceof HTMLTextAreaElement)) throw new Error("Embed composer is unavailable");
    input.value = "Find the active incident";
    input.form?.requestSubmit();
  });
  await expect.poll(() => embeddedAgent.evaluate((element) => element.shadowRoot?.textContent ?? ""))
    .toContain("I reviewed the current record for INC-104.");

  await page.goto("/agents");
  await page.getByRole("combobox", { name: "Select agent" }).selectOption({ label: "Operations Copilot · v1" });
  await expect(page.getByRole("textbox", { name: /^Agent name/ })).toHaveValue("Operations Copilot");
  await page.getByRole("combobox", { name: "Select agent" }).selectOption({ label: `${publishedName} · v2` });
  await expect(page.getByRole("textbox", { name: /^Agent name/ })).toHaveValue(publishedName);
});
