import { defineConfig } from "../app/node_modules/playwright/test";

export default defineConfig({
  testDir: "./tests",
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm --prefix ../app run dev",
    url: "http://127.0.0.1:3000/e2e/batch-upload",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
