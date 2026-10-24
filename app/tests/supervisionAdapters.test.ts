import assert from "node:assert/strict";
import { defaultSupervisorPeriod, displayCount, missingDepartments, reportRatio, verificationCounts, organizationPath, type SupervisorData, type SupervisorPeriod } from "../app/components/supervision/supervisionAdapters";
import { supervisorLoginPath } from "../lib/supervisorLoginPath";
import { SUPERVISOR_NAV_ITEMS, findNavItemByPathname } from "../app/components/workspace/nav";

assert.equal(displayCount(null), "—"); assert.equal(displayCount(0), "0");
const periods: SupervisorPeriod[] = [
  { fiscal_year: 2024, report_kind: "final", material_count: 4, uploaded: 4 },
  { fiscal_year: 2025, report_kind: "final", material_count: 19, uploaded: 19 },
];
assert.deepEqual(defaultSupervisorPeriod(new URLSearchParams(), periods), { fiscal_year: "2025", report_kind: "final" }, "裸入口应选择已有台账的年度和文种");
assert.equal(defaultSupervisorPeriod(new URLSearchParams("fiscal_year=2026&report_kind=budget"), periods), null, "主动查看未报送年度时不能被跳到历史年度");
assert.deepEqual(defaultSupervisorPeriod(new URLSearchParams("fiscal_year=2024"), periods), { report_kind: "final" });
assert.deepEqual(defaultSupervisorPeriod(new URLSearchParams("report_kind=final"), periods), { fiscal_year: "2025" });
assert.equal(defaultSupervisorPeriod(new URLSearchParams("report_kind=budget"), periods), null, "不能把明确的预算筛选换为决算");
assert.equal(defaultSupervisorPeriod(new URLSearchParams(), []), null, "授权范围没有台账时不猜年度");
assert.deepEqual(defaultSupervisorPeriod(new URLSearchParams(), [...periods, { fiscal_year: 2026, report_kind: "budget", material_count: 2, uploaded: 0 }]), { fiscal_year: "2026", report_kind: "budget" }, "应交但尚未上传的台账不能被隐藏");
const data = { summary: { empty_departments: null, partial_departments: null, expected: null, received_expected: 0 }, meta: { baseline_ready: false } } as SupervisorData;
assert.equal(missingDepartments(data), null); assert.equal(reportRatio(data), null);
data.summary.empty_departments = 3; data.summary.partial_departments = 2;
assert.equal(missingDepartments(data), 5);
data.meta.baseline_ready = true; data.summary.expected = 40; data.summary.received_expected = 31;
assert.equal(reportRatio(data), 31 / 40);
assert.equal(supervisorLoginPath("/queue", { is_read_only: true }), "/supervision");
assert.equal(supervisorLoginPath("//evil.test", { is_read_only: true }), "/supervision");
assert.equal(supervisorLoginPath("/supervision/issues?scope_id=a", { is_read_only: true }), "/supervision/issues?scope_id=a");
assert.equal(supervisorLoginPath("/queue", { is_read_only: false }), "/queue");
assert.equal(SUPERVISOR_NAV_ITEMS.length, 4);
assert.equal(findNavItemByPathname("/supervision/remediation")?.label, "整改追踪");
assert.equal(organizationPath("u", [{id:"d",name:"部门",level:"department",parent_id:null},{id:"u",name:"单位",level:"unit",parent_id:"d"}]), "部门 / 单位");
const findings = [{ issue_id: "a", title: "旧问题", severity: "high" }];
assert.equal(verificationCounts(findings, [], {}).complete, false, "消失不意味着已修复");
assert.equal(verificationCounts(findings, [], { a: "fixed" }).complete, true);
console.log("supervisionAdapters.test.ts passed: unknown/zero, baseline, read-only landing, four pages, explicit verification");
