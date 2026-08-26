import { defineConfig } from "../app/node_modules/playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

const externalServer = process.env.E2E_EXTERNAL_SERVER === "1";

export default defineConfig({
  testDir: "./tests",
  // CI 上允许重试：这些用例跑在 `next dev` 上，共享 runner 的 CPU 抢占会造成
  // 偶发的交互超时（已实测同一用例本机 9.9s 通过、CI 90s 超时）。
  // 根因缓解放在 scripts/run-e2e.cjs 的路由预热里，重试只是兜底，
  // 本机保持 0 重试，避免把真实回归掩盖成"重试一次就过"。
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  webServer: externalServer ? undefined : {
    command: "npm --prefix ../app run dev",
    url: `${baseURL}/e2e/batch-upload`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "ignore",
    stderr: "ignore",
  },
});
