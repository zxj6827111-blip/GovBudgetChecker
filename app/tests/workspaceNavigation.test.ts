import assert from "node:assert/strict";

import { computeNavBadgeCounts } from "../app/components/workspace/navBadges";
import { isAdminOnlyPathname, NAV_ITEMS } from "../app/components/workspace/nav";
import { resolveServiceHealthState } from "../app/components/workspace/serviceHealth";

// --- 导航配置结构性检查：9 项、分组正确、路径唯一、admin-only 项落在 admin 组 ------

assert.equal(NAV_ITEMS.length, 9, "REGRESSION: navigation must have exactly 8+1=9 items");

const workspaceItems = NAV_ITEMS.filter((item) => item.group === "workspace");
const adminItems = NAV_ITEMS.filter((item) => item.group === "admin");
assert.equal(workspaceItems.length, 6, "工作区组应有 5 项原型图项 + 1 项导出归档 = 6 项");
assert.equal(adminItems.length, 3, "管理组应有质量管理/规则与版本/系统设置 3 项");

for (const item of adminItems) {
  assert.equal(item.adminOnly, true, `admin 组的 ${item.id} 必须标记 adminOnly=true`);
}
for (const item of workspaceItems) {
  assert.equal(item.adminOnly, false, `workspace 组的 ${item.id} 不得标记 adminOnly=true`);
}

const hrefs = NAV_ITEMS.map((item) => item.href);
assert.equal(new Set(hrefs).size, hrefs.length, "REGRESSION: navigation hrefs must be unique, found a duplicate");

const ids = NAV_ITEMS.map((item) => item.id);
assert.equal(new Set(ids).size, ids.length, "REGRESSION: navigation ids must be unique");

// 「导出归档」必须存在且属于工作区组第 9 项（决策 2=a）
const archiveItem = NAV_ITEMS.find((item) => item.id === "archive");
assert.ok(archiveItem, "REGRESSION: 导出归档入口缺失（决策 2=a 明确要求新增第 9 个入口）");
assert.equal(archiveItem?.group, "workspace");
assert.equal(archiveItem?.adminOnly, false, "导出归档不是管理员专属项");

// --- isAdminOnlyPathname：反例覆盖 ------------------------------------------

assert.equal(isAdminOnlyPathname("/quality"), true);
assert.equal(isAdminOnlyPathname("/rules"), true);
assert.equal(isAdminOnlyPathname("/settings"), true);
assert.equal(isAdminOnlyPathname("/workbench"), false, "REGRESSION: 工作台不应被误判为管理员专属");
assert.equal(isAdminOnlyPathname("/archive"), false, "REGRESSION: 导出归档不应被误判为管理员专属");
assert.equal(isAdminOnlyPathname("/quality/some-sub-page"), true, "子路径也要继承父导航项的 adminOnly 判定");
assert.equal(isAdminOnlyPathname("/unknown-route"), false, "未知路径默认不拦截（不属于任何导航项）");

// --- computeNavBadgeCounts：核心反例——请求中/失败(null/undefined)不得显示 0 --------

assert.deepEqual(
  computeNavBadgeCounts(null),
  {},
  "REGRESSION: null(未拉到数据) 时角标必须整体为空对象，不得填充 0",
);
assert.deepEqual(
  computeNavBadgeCounts(undefined),
  {},
  "REGRESSION: undefined(未拉到数据) 时角标必须整体为空对象，不得填充 0",
);

// 空数组是"真实拉到数据、且确认当前没有任何任务"，必须返回真实的 0，不是空对象
assert.deepEqual(
  computeNavBadgeCounts([]),
  { analyzing: 0, review_required: 0 },
  "REGRESSION: 空数组是真实的零计数，必须显示 0，不能和 null/undefined 混为一谈",
);

const mixedJobs = [
  { job_id: "a", status: "processing" },
  { job_id: "b", status: "queued" },
  { job_id: "c", status: "review_required" },
  { job_id: "d", status: "needs_review" },
  { job_id: "e", status: "done" },
  { job_id: "f", status: "error" },
] as Array<{ job_id: string; status: string }>;
assert.deepEqual(
  computeNavBadgeCounts(mixedJobs),
  { analyzing: 2, review_required: 2 },
  "processing/queued 归入 analyzing；review_required/needs_review 归入 review_required；done/error 不计入任何角标",
);

// --- resolveServiceHealthState：三态反例，网络异常不得伪装成"服务正常" ---------------

assert.deepEqual(
  resolveServiceHealthState({ ok: true, status: 200 }, { status: "ok" }),
  { state: "healthy", label: "服务正常" },
);
assert.deepEqual(
  resolveServiceHealthState({ ok: false, status: 502 }, { status: "down", error: "connect failed" }),
  { state: "unhealthy", label: "服务异常" },
  "REGRESSION: 后端 502 时必须显示服务异常，不得显示服务正常",
);
assert.deepEqual(
  resolveServiceHealthState(null, null),
  { state: "unknown", label: "服务状态未知" },
  "REGRESSION: fetch 本身失败(拿不到任何响应)时不得显示服务正常，也不应武断宣称服务异常",
);
assert.deepEqual(
  resolveServiceHealthState({ ok: true, status: 200 }, { status: "unexpected" }),
  { state: "unhealthy", label: "服务异常" },
  "REGRESSION: HTTP 200 但响应体 status 字段不是字面 'ok' 时，仍不得显示服务正常",
);

console.log("workspaceNavigation.test.ts passed");
