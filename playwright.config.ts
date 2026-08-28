import { defineConfig, devices } from "@playwright/test";

const E2E_API_URL = "http://localhost:8100";
const E2E_WEB_URL = "http://localhost:3100";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: E2E_WEB_URL,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
  webServer: [
    {
      command: "uv run --project apps/api uvicorn alcuin_api.main:app --port 8100",
      url: `${E2E_API_URL}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ALCUIN_DATABASE_PATH: ":memory:",
        ALCUIN_CORS_ORIGINS: E2E_WEB_URL,
        ALCUIN_DEEPSEEK_API_KEY: "",
        ALCUIN_OPENAI_API_KEY: "",
      },
    },
    {
      command: "pnpm --filter @alcuin/web exec next dev --port 3100",
      url: `${E2E_WEB_URL}/studio`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ALCUIN_NEXT_DIST_DIR: ".next-e2e",
        NEXT_PUBLIC_ALCUIN_API_URL: E2E_API_URL,
        NEXT_PUBLIC_ALCUIN_WORKSPACE_ID: "ws_demo",
      },
    },
  ],
});
