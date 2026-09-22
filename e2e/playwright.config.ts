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
  expect: {
    // dev 模式的客户端导航要**现编译**目标路由的客户端资源：冷缓存（含
    // `rm -rf app/.next` 后首次运行）实测单次导航能到 4–5s，正好压在
    // Playwright 默认 5s 上。表现为随机的"某个下钻用例 URL 没变"，
    // 而且每次换一个 spec（谁先跨到未编译的路由谁中招）——容易被误读成
    // 业务回归。预热（scripts/run-e2e.cjs）只覆盖 HTML 路由，覆盖不到
    // 客户端导航，因此把断言超时统一放宽到这里一处，而不是在每个
    // `toHaveURL` 上各调一次。15s 仍然是"会失败"的界，只是不再随
    // dev 编译抖动。
    timeout: 15_000,
  },
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
