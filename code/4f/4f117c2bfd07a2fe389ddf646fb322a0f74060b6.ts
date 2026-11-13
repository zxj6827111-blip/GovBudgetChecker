import base from "../../e2e/playwright.config";
import path from "node:path";
export default { ...base, testDir: path.resolve(__dirname, "../../e2e/tests"), outputDir: path.resolve(__dirname, "browser-results"), use: { ...base.use, channel: "chrome" } };
