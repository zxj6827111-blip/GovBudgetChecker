import assert from "node:assert/strict";

import {
  MATERIAL_STATUSES,
  UNKNOWN_METRIC_TEXT,
  formatCoverageRate,
  formatMaterialTimestamp,
  presentCaliber,
  presentMaterialScope,
  presentMaterialStatus,
  presentRelationship,
  presentReportKind,
  presentStatusReason,
  presentSubjectKind,
  resolveDataBasisNotice,
} from "../lib/materialStatusPresentation";
import {
  DEFAULT_MATERIAL_FILTERS,
  SLOT_ABSENT_LABEL,
  UNAPPOINTED_DEPARTMENT_LABEL,
  buildDepartmentGroups,
  buildMaterialKpis,
  buildMaterialQuery,
  countDepartmentSubjects,
  normalizeFiscalYearInput,
  presentSlotCell,
  readCoveragePayload,
  readDepartmentMatrixPayload,
  readDistrictDepartmentsPayload,
  toDepartmentMatrixRows,
  toDistrictCardRows,
  type MaterialSlotSummary,
} from "../app/components/materials/materialLedgerAdapters";

/**
 * 材料台账（WP2-A）前端纯逻辑层测试。
 *
 * 拆到无 JSX 依赖的 .ts 文件里（与 workbenchAdapters 等同层做法一致）是因为
 * 这批函数承载的正是最容易出假结论的地方：
 * - "没有槽位"被显示成"缺失"；
 * - "完整率无法计算"被显示成 0；
 * - 三个页面各自硬编码状态颜色/文案；
 * - 未知状态码/原因码被显示成空白。
 * 这些必须能被断言，不能只靠肉眼看渲染结果。
 */

// --- 状态呈现：10 个状态全覆盖，且彼此可区分 --------------------------------

assert.equal(MATERIAL_STATUSES.length, 10, "状态取值域必须与 WP1 CHECK 约束一致（10 个）");

const statusPresentations = MATERIAL_STATUSES.map((status) => presentMaterialStatus(status));
for (const [index, presentation] of statusPresentations.entries()) {
  const status = MATERIAL_STATUSES[index];
  assert.ok(presentation.label.length > 0, `status=${status} 必须有中文文案`);
  assert.ok(presentation.description.length > 0, `status=${status} 必须有说明`);
  assert.ok(presentation.icon.length > 0, `status=${status} 必须有图标名`);
}

const labels = statusPresentations.map((presentation) => presentation.label);
assert.equal(
  new Set(labels).size,
  labels.length,
  "REGRESSION: 不同状态不得共用同一句文案（否则用户无法区分）",
);

assert.equal(presentMaterialStatus("missing").label, "逾期未上传");
assert.equal(presentMaterialStatus("failed").label, "处理失败");
assert.notEqual(
  presentMaterialStatus("missing").label,
  presentMaterialStatus("failed").label,
  "REGRESSION: 逾期未上传与处理失败是两件事，文案必须能区分",
);
assert.equal(presentMaterialStatus("not_applicable").label, "不适用");
assert.equal(presentMaterialStatus("mapping_required").label, "待确认归属");
assert.equal(presentMaterialStatus("not_due").label, "未到期");

// 未知状态码不能吞掉：必须原样带出来，便于发现后端新增状态
assert.equal(presentMaterialStatus("archived").label, "待确认：archived");
assert.notEqual(presentMaterialStatus(undefined).label.length, 0, "状态缺失时也要有可读文案");

// --- 原因码映射：§九 列出的十个 + 状态机自有的全部登记 -----------------------

const REQUIRED_REASON_MAPPINGS: Array<[string, string]> = [
  ["due_at_unknown", "截止时间未知"],
  ["caliber_conflict", "材料口径存在冲突"],
  ["slot_binding_conflict", "文件版本已绑定其他材料"],
  ["organization_missing", "缺少主体信息"],
  ["organization_unresolved", "主体无法确认"],
  ["organization_level_unknown", "主体层级无法确认"],
  ["kind_conflict", "预算/决算文种存在冲突"],
  ["year_conflict", "财政年度存在冲突"],
  ["kind_unknown", "文种无法识别"],
  ["year_unknown", "财政年度无法识别"],
];
for (const [code, label] of REQUIRED_REASON_MAPPINGS) {
  assert.equal(presentStatusReason(code), label, `reason=${code} 必须有正式中文映射`);
}

for (const code of [
  "due_not_reached",
  "due_exceeded",
  "applicability_unresolved",
  "applicability_marked_not_applicable",
  "identity_unresolved",
  "awaiting_analysis",
  "analysis_running",
  "analysis_failed",
  "findings_pending",
  "review_in_progress",
  "review_completed",
]) {
  assert.ok(
    presentStatusReason(code) && presentStatusReason(code) !== `待确认：${code}`,
    `reason=${code} 是 WP1 状态机会写入的码，必须登记文案`,
  );
}

assert.equal(presentStatusReason("some_new_reason"), "待确认：some_new_reason");
assert.equal(presentStatusReason(null), null, "没有原因时不显示占位文案");
assert.equal(presentStatusReason(""), null);

// --- 其它维度文案 ------------------------------------------------------------

assert.equal(presentSubjectKind("department"), "部门");
assert.equal(presentSubjectKind("unit"), "单位");
assert.equal(presentSubjectKind("government"), "政府");
assert.equal(presentSubjectKind("unknown"), "待确认");
assert.equal(presentMaterialScope("department_summary"), "部门汇总");
assert.equal(presentMaterialScope("unit_self"), "单位本级");
assert.equal(presentReportKind("budget"), "预算");
assert.equal(presentReportKind("final"), "决算");
assert.equal(presentReportKind("unknown"), "待确认");
assert.equal(presentCaliber("summary"), "汇总口径");
assert.equal(presentCaliber("self"), "本级口径");
assert.equal(presentRelationship("department_summary"), "部门汇总");
assert.equal(presentRelationship("head_unit"), "本部单位");
assert.equal(presentRelationship("subordinate_unit"), "直属单位");
assert.equal(presentRelationship("relationship_unknown"), "关系待确认");

// --- null 与 0 的显示差异 ----------------------------------------------------

assert.equal(formatCoverageRate(null), UNKNOWN_METRIC_TEXT);
assert.equal(formatCoverageRate(undefined), UNKNOWN_METRIC_TEXT);
assert.equal(formatCoverageRate(0), "0%", "真实的 0% 必须显示 0%，不能与 null 混同");
assert.equal(formatCoverageRate(0.987), "98.7%");
assert.equal(formatMaterialTimestamp(null), UNKNOWN_METRIC_TEXT);
assert.equal(formatMaterialTimestamp("not-a-date"), UNKNOWN_METRIC_TEXT);

// --- 口径提示文案随 expected_materials_ready 切换 ----------------------------

const legacyNotice = resolveDataBasisNotice(false);
assert.ok(legacyNotice.notice.includes("应收材料基线尚未导入"));
assert.ok(legacyNotice.emptyStateNotice.includes("不代表"));
const readyNotice = resolveDataBasisNotice(true);
assert.notEqual(readyNotice.notice, legacyNotice.notice, "基线就绪后必须换文案，不能仍说'尚未导入'");

// --- KPI 折算 ---------------------------------------------------------------

const counts = {
  not_due: 1,
  missing: 2,
  uploaded: 3,
  processing: 4,
  review_required: 5,
  reviewing: 6,
  completed: 7,
  not_applicable: 8,
  mapping_required: 9,
  failed: 10,
};
const kpis = buildMaterialKpis({
  slot_total: 55,
  status_counts: counts,
  budget_total: 20,
  final_total: 20,
  unknown_kind_total: 15,
  due_at_unknown: 30,
  jurisdiction_unknown_total: 1,
  expected_total: null,
  expected_budget_total: null,
  expected_final_total: null,
  coverage_rate: null,
  budget_coverage_rate: null,
  final_coverage_rate: null,
  missing_expected_total: null,
});
const kpiOf = (key: string) => kpis.find((kpi) => kpi.key === key)?.value;

assert.equal(kpiOf("slot_total"), 55, "已建材料取 slot_total");
assert.equal(kpiOf("uploaded"), 3);
assert.equal(kpiOf("review"), 11, "待复核 = review_required + reviewing");
assert.equal(kpiOf("completed"), 7);
assert.equal(kpiOf("mapping_required"), 9);
assert.equal(kpiOf("failed"), 10);
assert.equal(kpiOf("not_due"), 1);
assert.equal(kpiOf("missing"), 2);
assert.equal(
  kpis.length,
  8,
  "首页 KPI 第一屏 8 张（已建材料/已上传/待复核/已完成/待确认归属/处理失败/未到期/逾期未上传）",
);
assert.deepEqual(buildMaterialKpis(null), [], "没有数据时不产出 KPI（由页面显示加载/错误态）");

// --- 行映射 -----------------------------------------------------------------

const districtRows = toDistrictCardRows([
  {
    district_id: "d-1",
    district_name: "普陀区",
    slot_total: 4,
    status_counts: counts,
    budget_total: 2,
    final_total: 1,
    unknown_kind_total: 1,
    due_at_unknown: 3,
    updated_at: "2026-09-21T12:00:00Z",
    expected_total: null,
    expected_budget_total: null,
    expected_final_total: null,
    coverage_rate: null,
    budget_coverage_rate: null,
    final_coverage_rate: null,
    missing_expected_total: null,
  },
]);
assert.equal(districtRows[0].coverageRate, null, "无应收基线时完整率是 null（页面显示 —）");
assert.equal(districtRows[0].reviewRequired, 11);

const departmentRows = toDepartmentMatrixRows([
  {
    department_id: null,
    department_name: null,
    subject_count: 1,
    slot_total: 1,
    status_counts: counts,
    budget: { slot_total: 1, status_counts: counts },
    final: { slot_total: 0, status_counts: counts },
    unknown_kind_total: 0,
    missing: 2,
    not_due: 1,
    due_at_unknown: 0,
    updated_at: null,
    expected_total: null,
    expected_budget_total: null,
    expected_final_total: null,
    coverage_rate: null,
    budget_coverage_rate: null,
    final_coverage_rate: null,
    missing_expected_total: null,
  },
]);
assert.equal(
  departmentRows[0].departmentName,
  UNAPPOINTED_DEPARTMENT_LABEL,
  "没有主管部门的区级政府材料必须有明确名称，不能显示空白",
);
assert.equal(departmentRows[0].departmentId, null, "无主管部门的行不可下钻（id 为 null）");

// --- 分组 -------------------------------------------------------------------

const emptyGroups = {
  department_summary: [],
  head_unit: [],
  subordinate_units: [],
  relationship_unknown: [],
};
const groupKeys = buildDepartmentGroups(emptyGroups).map((group) => group.key);
assert.deepEqual(
  groupKeys,
  ["department_summary", "head_unit", "subordinate_units"],
  "三个分组固定展示；空的「关系待确认」不出现",
);
assert.equal(countDepartmentSubjects(emptyGroups), 0);

const subject = {
  subject_org_id: "s-1",
  subject_org_name: "某单位",
  subject_kind: "unit",
  relationship: "subordinate_unit",
  budget: { exists: false, slot: null },
  final: { exists: false, slot: null },
  unclassified: { exists: false, slot: null },
};
const filledGroups = {
  department_summary: [{ ...subject, relationship: "department_summary" }],
  head_unit: [{ ...subject, relationship: "head_unit" }],
  subordinate_units: [subject],
  relationship_unknown: [{ ...subject, relationship: "relationship_unknown" }],
};
assert.deepEqual(
  buildDepartmentGroups(filledGroups).map((group) => group.key),
  ["department_summary", "head_unit", "subordinate_units", "relationship_unknown"],
  "有'关系待确认'材料时必须显示该分组",
);
assert.equal(countDepartmentSubjects(filledGroups), 4);
assert.deepEqual(buildDepartmentGroups(null), []);

// --- 槽位单元格：exists=false 绝不显示"缺失" --------------------------------

const absentCell = presentSlotCell({ exists: false, slot: null });
assert.equal(absentCell.exists, false);
assert.equal(absentCell.emptyLabel, SLOT_ABSENT_LABEL);
assert.ok(
  !/缺失|逾期|未上传/.test(absentCell.emptyLabel),
  "REGRESSION: 没有槽位不得显示'缺失/逾期/未上传'（应收基线未建立时推不出这个结论）",
);
assert.equal(presentSlotCell(null).exists, false);
assert.equal(presentSlotCell(undefined).exists, false);

const slotStub: MaterialSlotSummary = {
  slot_id: "slot-1",
  slot_key: "key-1",
  jurisdiction_id: "d-1",
  jurisdiction_name: "普陀区",
  department_org_id: "dept-1",
  department_name: "某局",
  subject_org_id: "unit-1",
  subject_org_name: "某单位",
  subject_kind: "unit",
  material_scope: "unit_self",
  fiscal_year: 2025,
  report_kind: "budget",
  caliber: "self",
  caliber_conflict_candidate: null,
  status: "uploaded",
  status_reason: "awaiting_analysis",
  applicability_status: "applicable",
  applicability_note: null,
  due_at: null,
  current_document_version_id: 12,
  formal_issue_count: null,
  updated_at: "2026-09-21T12:00:00Z",
};
const presentCell = presentSlotCell({ exists: true, slot: slotStub });
assert.equal(presentCell.exists, true, "有槽位时必须渲染槽位卡");
assert.equal(presentCell.slot?.slot_id, "slot-1");

// --- 筛选与查询串 -----------------------------------------------------------

assert.equal(buildMaterialQuery(DEFAULT_MATERIAL_FILTERS), "", "默认筛选不产生查询串");
assert.equal(
  buildMaterialQuery({ fiscalYear: "2025", reportKind: "budget", status: "missing" }),
  "?fiscal_year=2025&report_kind=budget&status=missing",
);
assert.equal(
  buildMaterialQuery(DEFAULT_MATERIAL_FILTERS, { q: "民政", page: "1" }),
  "?q=%E6%B0%91%E6%94%BF&page=1",
);

assert.deepEqual(normalizeFiscalYearInput(""), { raw: "", value: "", valid: true });
assert.deepEqual(normalizeFiscalYearInput("2025"), { raw: "2025", value: "2025", valid: true });
assert.equal(normalizeFiscalYearInput("25").valid, false, "两位年份不得被猜成 2025");
assert.equal(normalizeFiscalYearInput("25").value, "", "非法输入不参与查询");
assert.equal(normalizeFiscalYearInput("1999").valid, false);
assert.equal(normalizeFiscalYearInput("2100").valid, false);
assert.equal(normalizeFiscalYearInput("abcd").valid, false);
assert.deepEqual(normalizeFiscalYearInput(" 2026 ").value, "2026");

// --- 响应解析：形状不符必须被识别为错误，而不是"空数据" ----------------------

const validCoverage = {
  ok: true,
  data: { summary: { slot_total: 0 }, districts: [] },
  meta: { data_basis: "existing_slots_only", expected_materials_ready: false, generated_at: "x" },
};
assert.ok(readCoveragePayload(validCoverage), "契约内的响应必须通过");

for (const invalid of [
  {},
  null,
  { ok: false, data: {}, meta: {} },
  { ok: true, data: {}, meta: {} },
  { ok: true, data: { summary: {}, districts: "nope" }, meta: {} },
  "<html>502</html>",
]) {
  assert.equal(
    readCoveragePayload(invalid),
    null,
    `不符合契约的响应必须返回 null（页面显示错误态），而不是被当成空台账：${JSON.stringify(invalid)?.slice(0, 40)}`,
  );
}

assert.ok(
  readDistrictDepartmentsPayload({
    ok: true,
    data: { district: { district_id: "d", district_name: "普陀区" }, items: [] },
    meta: {},
  }),
);
assert.equal(
  readDistrictDepartmentsPayload({ ok: true, data: { district: {}, items: "nope" }, meta: {} }),
  null,
);

assert.ok(
  readDepartmentMatrixPayload({
    ok: true,
    data: { department: { department_id: "d" }, fiscal_year: 2025, groups: { head_unit: [] } },
    meta: {},
  }),
);
assert.equal(
  readDepartmentMatrixPayload({ ok: true, data: { department: {}, fiscal_year: 2025 }, meta: {} }),
  null,
);

console.log("materialLedgerAdapters.test.ts passed");
