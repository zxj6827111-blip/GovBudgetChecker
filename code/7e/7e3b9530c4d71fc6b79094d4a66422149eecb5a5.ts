import { defineConfig } from "../../app/node_modules/playwright/test";
import baseConfig from "../../e2e/playwright.config";

export default defineConfig({
  ...baseConfig,
  testDir: "../../e2e/tests",
  use: { ...baseConfig.use, channel: "chrome" },
  webServer: undefined,
});
