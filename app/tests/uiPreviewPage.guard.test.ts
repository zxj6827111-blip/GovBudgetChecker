import assert from "node:assert/strict";

import { shouldBlockUiPreviewPage } from "../app/dev/ui-preview/guard";

// 断言意图：组件预览页在生产构建下必须不可达（Task 1 硬性要求"生产构建不得包含"）。
//
// shouldBlockUiPreviewPage() 是页面文件（app/dev/ui-preview/page.tsx）里
// `if (shouldBlockUiPreviewPage(process.env)) notFound();` 实际调用的同一个函数
// （不是复制一份逻辑在测试里重新判断一遍），因此这里对该函数的断言就是对页面
// 生产环境行为的直接验证；真实 HTTP 请求到达时是否真的返回 404 由 e2e 再补一层
// 端到端验证（不同验证层次，见 app/dev/ui-preview/guard.ts 顶部注释）。
//
// 正反对照：
// - 反例：生产环境且未开启白名单标志 -> 必须拦截（返回 true）；
// - 正例：开发环境，或生产环境显式打开标志 -> 必须放行（返回 false），
//   防止未来有人把条件写反导致开发环境也被误伤。

// --- 反例：生产环境、未设置标志 -> 必须拦截 -----------------------------------
assert.equal(
  shouldBlockUiPreviewPage({ NODE_ENV: "production" }),
  true,
  "REGRESSION: production without the opt-in flag must block the preview page",
);

// --- 反例变体：生产环境 + 显式设为非 'true' 的任意值 -> 仍必须拦截 ----------------
assert.equal(
  shouldBlockUiPreviewPage({ NODE_ENV: "production", GBC_ENABLE_E2E_PAGES: "false" }),
  true,
  "production with the flag explicitly set to false must still block",
);
assert.equal(
  shouldBlockUiPreviewPage({ NODE_ENV: "production", GBC_ENABLE_E2E_PAGES: "1" }),
  true,
  "only the exact string 'true' may open the gate, not truthy-looking values like '1'",
);

// --- 正例：开发环境 -> 必须放行，不能被误伤 -----------------------------------
assert.equal(
  shouldBlockUiPreviewPage({ NODE_ENV: "development" }),
  false,
  "development environment must never be blocked",
);
assert.equal(
  shouldBlockUiPreviewPage({}),
  false,
  "an entirely unset NODE_ENV (e.g. local ad-hoc script runs) must not be blocked",
);

// --- 正例：生产环境 + 显式开启标志 -> 必须放行（供人工在受控环境临时查看用） --------
assert.equal(
  shouldBlockUiPreviewPage({ NODE_ENV: "production", GBC_ENABLE_E2E_PAGES: "true" }),
  false,
  "production with the explicit opt-in flag must be allowed, matching the /e2e/* precedent",
);

console.log("uiPreviewPage.guard.test.ts passed");
