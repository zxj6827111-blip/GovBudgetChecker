import { defineConfig } from "../../app/node_modules/playwright/test";
import base from "../../e2e/playwright.config";
import { resolve } from "node:path";
export default defineConfig({ ...base, testDir: resolve(__dirname, "../../e2e/tests"), webServer: undefined, workers: 1, outputDir: resolve(__dirname, "test-results"), use: { ...base.use, launchOptions: { executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" } } });
