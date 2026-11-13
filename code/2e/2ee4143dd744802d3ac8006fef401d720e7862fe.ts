import { defineConfig } from "../../app/node_modules/playwright/test";
import base from "../../e2e/playwright.config";

export default defineConfig({
  ...base,
  testDir: "../../e2e/tests",
  webServer: undefined,
  retries: 0,
  workers: 1,
  use: { ...base.use, baseURL: "http://127.0.0.1:3000", channel: "chrome", viewport: { width: 1440, height: 900 } },
  reporter: [["list"], ["json", { outputFile: "e2e-results.json" }]],
  outputDir: "test-results",
});
