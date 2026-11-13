import { defineConfig } from "../../app/node_modules/playwright/test";
import { join } from "node:path";
import base from "../../e2e/playwright.config";

export default defineConfig({
  ...base,
  testDir: join(__dirname, "../../e2e/tests"),
  webServer: undefined,
  workers: 1,
  reporter: "line",
  outputDir: join(__dirname, "results"),
  use: {...base.use, channel: "chrome", screenshot: "only-on-failure"},
});
