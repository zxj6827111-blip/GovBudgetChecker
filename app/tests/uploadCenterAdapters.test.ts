import assert from "node:assert/strict";

import {
  applyManualConfirmationOverride,
  checkUploadLimit,
  derivePreflightStatus,
  formatAttributionBreadcrumb,
  formatFileSizeMb,
  formatPageCountText,
  formatUnitScopeHint,
  listPreflightConfirmationReasons,
  selectDepartmentOptions,
  selectUnitOptionsForDepartment,
  validateAttribution,
  type OrganizationRecordLike,
} from "../app/components/workspace/uploadCenterAdapters";

// 本文件测试 Task 5 上传中心的纯逻辑层（预检状态映射/上传限制校验/三步归属
// 必填联动/归属路径面包屑/组织拉平与同名区分）。

// --- derivePreflightStatus：三态映射，且必须来自真实字段而非随机/文件名猜测 -----

assert.equal(derivePreflightStatus(null), "failed", "REGRESSION: 预检请求失败(null)必须映射为 failed，不得默认成 passed");
assert.equal(derivePreflightStatus(undefined), "failed");

assert.equal(
  derivePreflightStatus({ report_year: 2026, doc_type: "dept_budget", current: { organization_id: "org-1", organization_name: "市教育局", level: "department", confidence: 0.9 } }),
  "passed",
  "年度/类型/高置信度组织匹配三者齐全时才是 passed",
);

assert.equal(
  derivePreflightStatus({ report_year: null, doc_type: "dept_budget", current: { organization_id: "org-1", organization_name: "x", level: "department", confidence: 0.9 } }),
  "needs_confirmation",
  "REGRESSION: 年度未识别到时必须是 needs_confirmation，不得因为其它字段齐全而判定为 passed",
);

assert.equal(
  derivePreflightStatus({ report_year: 2026, doc_type: null, current: { organization_id: "org-1", organization_name: "x", level: "department", confidence: 0.9 } }),
  "needs_confirmation",
  "文档类型未识别到时必须是 needs_confirmation",
);

assert.equal(
  derivePreflightStatus({ report_year: 2026, doc_type: "dept_budget", current: null }),
  "needs_confirmation",
  "REGRESSION: current 为 null（后端置信度 < 0.6）时必须是 needs_confirmation，这是真实低置信度判定，不是随机猜测",
);

assert.equal(
  derivePreflightStatus({ report_year: 2026, doc_type: "dept_budget" }),
  "needs_confirmation",
  "current 字段缺失（未启用组织匹配）同样视为需要确认",
);

// --- checkUploadLimit：真实限制值必须由调用方传入，本函数不内置 200/30 --------

assert.equal(
  checkUploadLimit(10 * 1024 * 1024, 30, 100, 800),
  null,
  "10MB/100页 在 30MB/800页 限制内应通过",
);

const sizeViolation = checkUploadLimit(50 * 1024 * 1024, 30, 100, 800);
assert.ok(sizeViolation, "50MB 超过 30MB 限制应产生违规");
assert.equal(sizeViolation?.type, "size");
assert.match(sizeViolation?.message ?? "", /30 MB/, "违规文案必须包含真实限制值 30MB");
assert.doesNotMatch(sizeViolation?.message ?? "", /200 MB/, "REGRESSION: 违规文案绝不能出现原型图示例值 200MB");

const pagesViolation = checkUploadLimit(1024, 30, 900, 800);
assert.ok(pagesViolation);
assert.equal(pagesViolation?.type, "pages");
assert.match(pagesViolation?.message ?? "", /800 页/);

// 页数未知（尚未解析）时不应误判为超限
assert.equal(checkUploadLimit(1024, 30, null, 800), null, "页数未知时不应产生页数违规");
assert.equal(checkUploadLimit(1024, 30, undefined, 800), null);

// 反例：调用方若真的传入了 200（配置误用），函数如实按 200 校验，不会替它纠正成 30——
// 纠正的责任在"从哪里读取这两个数字"这一步，不在这个纯校验函数。
assert.equal(checkUploadLimit(100 * 1024 * 1024, 200, 100, 800), null, "调用方传 200 时函数按 200 校验（纠正责任不在本函数）");

// --- validateAttribution：三步归属必填与联动 --------------------------------

assert.deepEqual(
  validateAttribution({ departmentId: "", fileLevel: null, unitId: "" }),
  { isComplete: false, missingStep: 1, reason: "请先选择预算主管部门" },
);

assert.deepEqual(
  validateAttribution({ departmentId: "dept-1", fileLevel: null, unitId: "" }),
  { isComplete: false, missingStep: 2, reason: "请选择文件层级（部门汇总文件或单位文件）" },
);

assert.deepEqual(
  validateAttribution({ departmentId: "dept-1", fileLevel: "unit", unitId: "" }),
  { isComplete: false, missingStep: 3, reason: "单位文件必须选择所属预算单位" },
  "REGRESSION: 单位文件必须选单位，未选完不得允许提交",
);

assert.deepEqual(
  validateAttribution({ departmentId: "dept-1", fileLevel: "unit", unitId: "unit-1" }),
  { isComplete: true, missingStep: null, reason: "" },
);

assert.deepEqual(
  validateAttribution({ departmentId: "dept-1", fileLevel: "department_summary", unitId: "" }),
  { isComplete: true, missingStep: null, reason: "" },
  "REGRESSION: 部门汇总文件不应要求选单位，未选单位时应视为已完成",
);

// --- formatAttributionBreadcrumb：同名场景必须靠括注区分，不得合并成一段 -------

assert.equal(formatAttributionBreadcrumb(null, null, null), "尚未选择归属");

const department = { id: "dept-caizheng", name: "上海市普陀区财政局" };
assert.equal(
  formatAttributionBreadcrumb(department, "department_summary", null),
  "上海市普陀区财政局（部门）",
);

const headUnit = { id: "unit-caizheng-head", name: "上海市普陀区财政局" }; // 与部门同名
assert.equal(
  formatAttributionBreadcrumb(department, "unit", headUnit),
  "上海市普陀区财政局（部门） › 上海市普陀区财政局（本级单位）",
  "REGRESSION: 部门与本级单位同名时必须分别用（部门）/（本级单位）括注区分，不得合并成一段文字",
);

const directUnit = { id: "unit-guoku", name: "上海市普陀区国库收付中心" };
assert.equal(
  formatAttributionBreadcrumb(department, "unit", directUnit),
  "上海市普陀区财政局（部门） › 上海市普陀区国库收付中心（直属单位）",
);

// --- selectDepartmentOptions / selectUnitOptionsForDepartment：同名不合并 -----

const orgs: OrganizationRecordLike[] = [
  { id: "dept-caizheng", name: "上海市普陀区财政局", level: "department", parent_id: "district-putuo" },
  { id: "unit-caizheng-head", name: "上海市普陀区财政局", level: "unit", parent_id: "dept-caizheng" }, // 同名本级单位
  { id: "unit-guoku", name: "上海市普陀区国库收付中心", level: "unit", parent_id: "dept-caizheng" },
  { id: "unit-caizhengsuo", name: "上海市普陀区财政局财政所", level: "unit", parent_id: "dept-caizheng" },
  { id: "dept-jiaoyu", name: "上海市普陀区教育局", level: "department", parent_id: "district-putuo" },
  { id: "unit-other-dept", name: "别的部门下的单位", level: "unit", parent_id: "dept-jiaoyu" },
];

const departmentOptions = selectDepartmentOptions(orgs);
assert.equal(departmentOptions.length, 2, "REGRESSION: 部门与其同名本级单位是两条独立记录，部门列表不得把二者合并/去重成一条");
assert.ok(departmentOptions.some((d) => d.id === "dept-caizheng"));
assert.ok(departmentOptions.some((d) => d.id === "dept-jiaoyu"));

const units = selectUnitOptionsForDepartment(orgs, "dept-caizheng");
assert.equal(units.length, 3, "只应包含 dept-caizheng 下的单位，不包含 dept-jiaoyu 下的单位");
assert.equal(units[0].id, "unit-caizheng-head", "REGRESSION: 本级单位（即便与部门同名）必须排在单位下拉第一项");
assert.ok(!units.some((u) => u.id === "unit-other-dept"), "别的部门下的单位不应混入");

assert.equal(formatUnitScopeHint("上海市普陀区财政局"), "仅显示上海市普陀区财政局所属单位");
assert.equal(formatUnitScopeHint(""), "请先选择预算主管部门");

// --- formatFileSizeMb / formatPageCountText -------------------------------

assert.equal(formatFileSizeMb(18.4 * 1024 * 1024), "18.4 MB");
assert.equal(formatFileSizeMb(1024 * 1024), "1.0 MB");

assert.equal(formatPageCountText(48), "48 页");
assert.equal(formatPageCountText(null), "—", "REGRESSION: 页数未知(尚未从preflight拿到)必须显示 em dash，不得猜一个数字");
assert.equal(formatPageCountText(undefined), "—");
assert.notEqual(formatPageCountText(null), "0 页", "REGRESSION: 页数未知绝不能显示成 0 页");

// --- listPreflightConfirmationReasons / applyManualConfirmationOverride ------
// 前置修复 1：分析前确认闸门。needs_confirmation 必须有真实可用的补齐路径，
// 且补齐后的状态转换必须真实生效（不是前端单方面改标记）。

assert.deepEqual(listPreflightConfirmationReasons(null), [], "无 preflight 响应时没有可列出的缺失原因");
assert.deepEqual(listPreflightConfirmationReasons(undefined), []);

assert.deepEqual(
  listPreflightConfirmationReasons({
    report_year: 2026,
    doc_type: "dept_budget",
    current: { organization_id: "org-1", organization_name: "x", level: "department", confidence: 0.9 },
  }),
  [],
  "三项齐全时不应列出任何缺失原因",
);

assert.deepEqual(
  listPreflightConfirmationReasons({ report_year: null, doc_type: "dept_budget", current: null }),
  ["missing_report_year", "low_confidence_org"],
  "REGRESSION: 必须精确列出缺失的是哪几项（年份+组织），不是笼统的一个布尔值，否则 UI 无法告诉用户具体缺什么",
);

assert.deepEqual(
  listPreflightConfirmationReasons({ report_year: 2026, doc_type: null, current: { organization_id: "o", organization_name: "x", level: "department", confidence: 0.9 } }),
  ["missing_doc_type"],
);

// applyManualConfirmationOverride：只补缺失字段，不覆盖已识别正确的字段 ---------

const originalWithYearOnly: import("../app/components/workspace/uploadCenterAdapters").PreflightResponseLike = {
  report_year: 2026,
  doc_type: null,
  current: null,
};

const afterOverride = applyManualConfirmationOverride(originalWithYearOnly, {
  docType: "dept_final",
  organizationId: "org-9",
  organizationName: "上海市普陀区财政局",
});
assert.equal(afterOverride.report_year, 2026, "已经识别正确的年份不应被覆盖值污染");
assert.equal(afterOverride.doc_type, "dept_final", "缺失的文档类型必须被覆盖值补齐");
assert.equal(afterOverride.current?.organization_id, "org-9", "缺失的组织必须被覆盖值补齐");
assert.equal(afterOverride.current?.confidence, 1, "人工选择视为完全确认，置信度记为 1");

// 补齐后必须能通过 derivePreflightStatus 重新判定为 passed（状态转换真实生效，
// 不是前端单方面改一个标记而后端仍收到空值）——这是"反例：补齐后必须能提交"的核心。
assert.equal(
  derivePreflightStatus(afterOverride),
  "passed",
  "REGRESSION: 补齐全部缺失字段后，derivePreflightStatus 必须重新判定为 passed，否则闸门变成死路",
);

// 反例：只补了一部分缺失字段时仍应是 needs_confirmation（不能因为补了一项就整体放行）
const partiallyFixed = applyManualConfirmationOverride(
  { report_year: null, doc_type: null, current: null },
  { reportYear: "2026" },
);
assert.equal(
  derivePreflightStatus(partiallyFixed),
  "needs_confirmation",
  "REGRESSION: 只补齐年份、doc_type/组织仍缺失时，必须仍是 needs_confirmation，不能整体误判为 passed",
);

// override 为 null/undefined 时原样返回（幂等，不抛错）
assert.deepEqual(applyManualConfirmationOverride(originalWithYearOnly, null), originalWithYearOnly);
assert.deepEqual(applyManualConfirmationOverride(originalWithYearOnly, undefined), originalWithYearOnly);

// response 为 null/undefined 时返回空对象基底（不抛错），覆盖值仍能叠加上去
const fromEmptyBase = applyManualConfirmationOverride(null, { reportYear: "2025" });
assert.equal(fromEmptyBase.report_year, 2025);

console.log("uploadCenterAdapters.test.ts passed");
