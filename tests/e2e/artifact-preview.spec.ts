import { expect, test } from "@playwright/test";

import { ARTIFACT_SANDBOX, artifactPreviewPolicy, buildArtifactPreview } from "../../apps/web/features/studio/artifacts/artifact-html";

test("HTML artifacts support local interaction while isolating parent and network access", async ({ page }) => {
  const networkRequests: string[] = [];
  page.on("request", (request) => { if (request.url().startsWith("https://artifact-external.invalid")) networkRequests.push(request.url()); });
  await page.setContent('<main id="studio-private">Studio secret</main>');
  await page.evaluate(({ builder, policy, sandbox }) => {
    const policyFunction = new Function(`return (${policy});`)();
    const build = new Function("artifactPreviewPolicy", `return (${builder});`)(policyFunction) as typeof buildArtifactPreview;
    const frame = document.createElement("iframe");
    frame.title = "Interactive artifact";
    frame.setAttribute("sandbox", sandbox);
    frame.srcdoc = build(`<!doctype html><html><head>
      <meta http-equiv="refresh" content="0;url=https://artifact-external.invalid/refresh">
      <script src="https://artifact-external.invalid/script.js"></script>
      <link rel="stylesheet" href="https://artifact-external.invalid/style.css">
      </head><body><button id="increment">Add one</button><output id="count">0</output><output id="isolation"></output><output id="network"></output>
      <img src="https://artifact-external.invalid/pixel"><iframe src="https://artifact-external.invalid/frame"></iframe>
      <script>
        document.getElementById('increment').onclick=()=>{document.getElementById('count').textContent=String(Number(document.getElementById('count').textContent)+1)};
        try { document.getElementById('isolation').textContent=parent.document.getElementById('studio-private').textContent; } catch { document.getElementById('isolation').textContent='Parent blocked'; }
        fetch('https://artifact-external.invalid/api').then(()=>document.getElementById('network').textContent='Unexpected network').catch(()=>document.getElementById('network').textContent='Network blocked');
      </script></body></html>`, { interactive: true, dark: false });
    document.body.appendChild(frame);
  }, { builder: buildArtifactPreview.toString(), policy: artifactPreviewPolicy.toString(), sandbox: ARTIFACT_SANDBOX });

  const frame = page.frameLocator('iframe[title="Interactive artifact"]');
  await expect(frame.locator("#isolation")).toHaveText("Parent blocked");
  await expect(frame.locator("#network")).toHaveText("Network blocked");
  await frame.getByRole("button", { name: "Add one" }).click();
  await expect(frame.locator("#count")).toHaveText("1");
  await expect(frame.locator("iframe,script[src],link,meta[http-equiv='refresh']")).toHaveCount(0);
  await expect(page.locator("#studio-private")).toHaveText("Studio secret");
  expect(networkRequests).toEqual([]);
});

test("streaming HTML is visible without executing unfinished scripts", async ({ page }) => {
  await page.setContent("<main>Studio</main>");
  await page.evaluate(({ builder, policy, sandbox }) => {
    const policyFunction = new Function(`return (${policy});`)();
    const build = new Function("artifactPreviewPolicy", `return (${builder});`)(policyFunction) as typeof buildArtifactPreview;
    const frame = document.createElement("iframe");
    frame.title = "Streaming artifact";
    frame.setAttribute("sandbox", sandbox);
    frame.srcdoc = build('<h1>Working dashboard</h1><output id="state">Not executed</output><script>document.getElementById("state").textContent="Executed"</script>', { interactive: false, dark: true });
    document.body.appendChild(frame);
  }, { builder: buildArtifactPreview.toString(), policy: artifactPreviewPolicy.toString(), sandbox: ARTIFACT_SANDBOX });
  const frame = page.frameLocator('iframe[title="Streaming artifact"]');
  await expect(frame.getByRole("heading")).toHaveText("Working dashboard");
  await expect(frame.locator("#state")).toHaveText("Not executed");
  await expect(frame.locator("script")).toHaveCount(0);
  expect(await frame.locator("html").evaluate((node) => getComputedStyle(node).colorScheme)).toBe("dark");
});
