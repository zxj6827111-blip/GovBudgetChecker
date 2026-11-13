import base from "../../e2e/playwright.config";

export default {
  ...base,
  testDir: "../../e2e/tests",
  webServer: undefined,
  use: { ...base.use, channel: "chrome", trace: "retain-on-failure" },
};
