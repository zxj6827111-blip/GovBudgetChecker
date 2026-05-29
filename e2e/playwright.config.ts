import { defineConfig } from "../app/node_modules/playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm --prefix ../app run dev",
    url: `${baseURL}/e2e/batch-upload`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
