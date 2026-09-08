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
      command: "uv run --project apps/api uvicorn --app-dir apps/api/tests/fixtures e2e_app:app --port 8100",
      url: `${E2E_API_URL}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ALCUIN_DATABASE_URL:
          process.env.ALCUIN_DATABASE_URL ??
          "postgresql://alcuin:alcuin-test@127.0.0.1:55432/alcuin_test",
        ALCUIN_CORS_ORIGINS: E2E_WEB_URL,
        ALCUIN_DEEPSEEK_API_KEY: "e2e-provider-key",
        ALCUIN_DEEPSEEK_BASE_URL: "http://127.0.0.1:9412/v1",
        ALCUIN_DEEPSEEK_PROTOCOL: "chat_completions",
        ALCUIN_EXTENSION_ALLOW_PRIVATE_NETWORKS: "true",
        ALCUIN_SEARXNG_URL: "http://127.0.0.1:9412",
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
    {
      command: "uv run --project apps/api uvicorn examples.records_api.app:app --port 9412",
      url: "http://127.0.0.1:9412/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
