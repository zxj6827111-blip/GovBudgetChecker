import { defineConfig } from "../../app/node_modules/playwright/test";
import base from "../../e2e/playwright.config";
import path from "node:path";

export default defineConfig({
  ...base,
  testDir: path.resolve(__dirname, "../../e2e/tests"),
  webServer: undefined,
  use: { ...base.use, channel: "chrome" },
});
