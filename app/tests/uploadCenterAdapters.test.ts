import assert from "node:assert/strict";

import {
  applyManualConfirmationOverride,
  buildUploadFormFields,
  checkUploadLimit,
  derivePreflightStatus,
  describeUploadFailure,
  formatAttributionBreadcrumb,
  formatFileSizeMb,
  formatPageCountText,
  formatUploadFailureText,
  formatUnitScopeHint,
  listPreflightConfirmationReasons,
  selectDepartmentOptions,
  selectUnitOptionsForDepartment,
  validateAttribution,
  type OrganizationRecordLike,
  describeAnalyzeStartFailure,
  summarizeSubmitOutcome,
  type SubmitFileOutcome,
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

// --- buildUploadFormFields（修复 A1）：doc_type / fiscal_year 何时发送 ------------
// 反例（核心断言）：预设 docType 默认值为空（不预填 dept_budget），且识别不到时
// 必须返回 null——null 语义是"该字段整个不进 FormData"，让后端用封面识别结果。
const EMPTY_PRESETS = { organizationId: "", year: "", docType: "" };

assert.deepEqual(
  buildUploadFormFields({ report_year: 2024, doc_type: "dept_final" }, EMPTY_PRESETS),
  { docType: "dept_final", fiscalYear: "2024" },
  "识别到的值直接作为提交值",
);

assert.deepEqual(
  buildUploadFormFields({ report_year: null, doc_type: null }, EMPTY_PRESETS),
  { docType: null, fiscalYear: null },
  "REGRESSION: 识别不到且未预设时 doc_type/year 必须为 null（不发送），绝不能回落成 dept_budget 或空字符串——这正是实机决算材料必然 422 的根因",
);

assert.deepEqual(
  buildUploadFormFields({ report_year: null, doc_type: null }, { ...EMPTY_PRESETS, docType: "dept_final" }),
  { docType: "dept_final", fiscalYear: null },
  "用户显式预设了类型时按预设提交（年份仍不发送）",
);

assert.deepEqual(
  buildUploadFormFields({ report_year: null, doc_type: null }, { ...EMPTY_PRESETS, year: "2025" }),
  { docType: null, fiscalYear: "2025" },
  "用户显式预设了年份时按预设提交（类型仍不发送）",
);

// 识别值优先于预设（预设不应该把已识别正确的字段改掉）
assert.deepEqual(
  buildUploadFormFields({ report_year: 2024, doc_type: "dept_final" }, { ...EMPTY_PRESETS, docType: "dept_budget", year: "2025" }),
  { docType: "dept_final", fiscalYear: "2024" },
  "REGRESSION: 识别到的值必须优先于批量预设，预设不覆盖已识别正确的字段",
);

assert.deepEqual(
  buildUploadFormFields(undefined, EMPTY_PRESETS),
  { docType: null, fiscalYear: null },
  "无 preflight 响应且未预设时不发送任何字段",
);

// --- describeUploadFailure（修复 A2）：结构化错误 → 可读可行动的中文 -----------

// 422 report_type_conflict：必须显示提交值 vs 封面识别值（含中文标签），给行动建议
const typeConflict = describeUploadFailure({
  filename: "上海市普陀区人民政府办公室2024年度部门决算.pdf",
  status: 422,
  payload: {
    detail: {
      error: "report_type_conflict",
      submitted_doc_type: "dept_budget",
      detected_doc_type: "dept_final",
      message: "Submitted document type conflicts with PDF cover metadata.",
    },
  },
});
assert.match(typeConflict.title, /文档类型与封面识别不一致/);
assert.match(typeConflict.detail ?? "", /部门预算（dept_budget）/);
assert.match(typeConflict.detail ?? "", /部门决算（dept_final）/);
assert.match(typeConflict.suggestion ?? "", /改为部门决算|清空类型/);
assert.ok(!JSON.stringify(typeConflict).includes("conflicts with PDF cover"), "不得把英文 detail 原样 dump 给用户");

// 422 report_year_conflict：显示提交年份 vs 封面识别年份
const yearConflict = describeUploadFailure({
  filename: "x.pdf",
  status: 422,
  payload: {
    detail: {
      error: "report_year_conflict",
      submitted_year: 2025,
      detected_year: 2024,
      message: "Submitted fiscal year conflicts with PDF cover metadata.",
    },
  },
});
assert.match(yearConflict.title, /年份与封面识别不一致/);
assert.match(yearConflict.detail ?? "", /提交年份：2025/);
assert.match(yearConflict.detail ?? "", /封面识别：2024/);

// 413 体积超限：必须显示实际限制值与实际文件值
const tooLarge = describeUploadFailure({
  filename: "大文件.pdf",
  status: 413,
  payload: { detail: "File exceeds 30MB limit" },
  fileSizeBytes: 45 * 1024 * 1024,
  maxUploadMb: 30,
});
assert.match(tooLarge.title, /大小超过系统限制/);
assert.match(tooLarge.detail ?? "", /45\.0 MB/);
assert.match(tooLarge.detail ?? "", /30 MB/);
assert.doesNotMatch(tooLarge.detail ?? "", /200 MB/, "REGRESSION: 限制值来自真实配置，绝不显示原型图示例值 200MB");

// 413 页数超限：后端中文 detail 自带实际页数与上限，原样保留关键值
const tooManyPages = describeUploadFailure({
  filename: "y.pdf",
  status: 413,
  payload: { detail: "PDF页数超过限制：900 页，当前上限为 800 页" },
});
assert.match(tooManyPages.title, /页数超过系统限制/);
assert.match(tooManyPages.detail ?? "", /900 页/);
assert.match(tooManyPages.detail ?? "", /800 页/);

// 409 重复上传
const duplicate = describeUploadFailure({
  filename: "z.pdf",
  status: 409,
  payload: { detail: "检测到重复上传：z.pdf（任务 job-123）" },
});
assert.match(duplicate.title, /重复/);
assert.match(duplicate.detail ?? "", /job-123/);
assert.match(duplicate.suggestion ?? "", /处理队列/);

// 403 / 404 / 503 / 429 / 401
assert.match(describeUploadFailure({ filename: "a.pdf", status: 403, payload: { detail: "organization access denied" } }).title, /没有权限/);
assert.match(describeUploadFailure({ filename: "a.pdf", status: 404, payload: { detail: "organization not found" } }).title, /组织不存在/);
assert.match(describeUploadFailure({ filename: "a.pdf", status: 503, payload: { detail: "organization service unavailable" } }).title, /组织服务暂不可用/);
assert.match(describeUploadFailure({ filename: "a.pdf", status: 429, payload: { detail: "Too Many Requests" } }).title, /过于频繁/);
assert.match(describeUploadFailure({ filename: "a.pdf", status: 401, payload: { detail: "not logged in" } }).title, /登录状态已过期/);

// 400 非 PDF
assert.match(
  describeUploadFailure({ filename: "a.pdf", status: 400, payload: { detail: "File does not appear to be a valid PDF (invalid signature)" } }).title,
  /不是有效的 PDF/,
);

// 反例：未知状态码不得把 detail 原样 dump（可能含内部路径），只显示状态码
const unknownError = describeUploadFailure({
  filename: "a.pdf",
  status: 500,
  payload: { detail: "Internal error at C:\\\\app\\\\uploads\\\\job-1\\\\src.pdf" },
});
assert.ok(!JSON.stringify(unknownError).includes("C:\\\\app"), "未知错误的 detail 不得进入用户可见文案");
assert.match(unknownError.title, /HTTP 500/);

// payload 为 null（响应体不是 JSON）时不抛错，仍给出可读映射
const noBody = describeUploadFailure({ filename: "a.pdf", status: 422, payload: null });
assert.ok(noBody.title.length > 0);
assert.match(noBody.title, /422|校验/);

// --- formatUploadFailureText：结构化消息拼接（标题/关键值/建议 三段） -----------
const conflictText = formatUploadFailureText(typeConflict);
assert.match(conflictText, /文档类型与封面识别不一致/);
assert.match(conflictText, /提交类型：部门预算（dept_budget）；封面识别：部门决算（dept_final）/);
assert.match(conflictText, /^建议：/m, "建议行必须以「建议：」开头，用户能据此行动");
assert.ok(conflictText.includes("\n"), "多段消息用换行分隔，供 whitespace-pre-line 渲染");



// ===========================================================================
// 修复 2：上传成功后真正触发分析——分析启动失败映射 + 批量提交结果汇总
// ===========================================================================

// --- describeAnalyzeStartFailure：核心红线——"上传成功但分析启动失败"必须与
//     "上传失败"是可区分的两个状态，文案里必须带上"上传成功"前提 -------------

const analyzeConflict = describeAnalyzeStartFailure({
  filename: "决算.pdf",
  status: 500,
  payload: { detail: "internal error" },
});
assert.match(analyzeConflict.title, /上传成功，但分析启动失败/, "必须如实说明文件已上传成功");
assert.match(analyzeConflict.title, /HTTP 500/);
assert.match(String(analyzeConflict.suggestion), /处理队列/, "必须指明任务已在队列中、可重试");

// 409：已在分析中，不算错误地重复启动
assert.match(
  describeAnalyzeStartFailure({ filename: "a.pdf", status: 409, payload: null }).title,
  /已在分析中，无需重复启动/,
);
// 404：任务不存在
assert.match(
  describeAnalyzeStartFailure({ filename: "a.pdf", status: 404, payload: null }).title,
  /任务不存在/,
);
// 429：限流
assert.match(
  describeAnalyzeStartFailure({ filename: "a.pdf", status: 429, payload: null }).title,
  /过于频繁/,
);
// status=0：网络异常（请求根本没发出去）
const networkFailure = describeAnalyzeStartFailure({ filename: "a.pdf", status: 0, payload: null });
assert.match(networkFailure.title, /分析启动请求发送失败/);
assert.ok(!JSON.stringify(networkFailure).includes("HTTP 0"), "网络异常不得误报成 HTTP 状态码");

// 反例：文案不得与"上传失败"混淆——describeUploadFailure 的 500 文案没有"上传成功"前提
const uploadFailText = formatUploadFailureText(
  describeUploadFailure({ filename: "a.pdf", status: 500, payload: null }),
);
assert.ok(!uploadFailText.includes("上传成功"), "上传失败文案不得出现'上传成功'");

// --- summarizeSubmitOutcome：逐文件隔离 + 两类失败如实区分 ------------------

const okOutcome: SubmitFileOutcome = {
  entryId: "e1",
  filename: "预算A.pdf",
  uploadOk: true,
  analysisStarted: true,
  jobId: "job-1",
  failureText: null,
};

// 全部成功：summaryText 必须为 null（成功路径不渲染任何错误容器）
const allOk = summarizeSubmitOutcome([okOutcome, { ...okOutcome, entryId: "e2", jobId: "job-2" }]);
assert.equal(allOk.allSucceeded, true);
assert.deepEqual(allOk.uploadedJobIds, ["job-1", "job-2"]);
assert.equal(allOk.summaryText, null, "REGRESSION: 全部成功时不得产生任何错误文案");
assert.deepEqual(allOk.failedEntryIds, []);

// 反例：分析启动失败必须与上传失败分开表述
const analyzeFailed: SubmitFileOutcome = {
  entryId: "e2",
  filename: "决算B.pdf",
  uploadOk: true,
  analysisStarted: false,
  jobId: "job-2",
  failureText: "文件 决算B.pdf 上传成功，但分析启动失败（HTTP 500）\n建议：任务已在处理队列中，可稍后重试分析。",
};
const mixedAnalyze = summarizeSubmitOutcome([okOutcome, analyzeFailed]);
assert.equal(mixedAnalyze.allSucceeded, false);
assert.deepEqual(mixedAnalyze.uploadedJobIds, ["job-1", "job-2"], "分析启动失败的文件任务仍已创建，必须计入 uploadedJobIds");
assert.deepEqual(mixedAnalyze.failedEntryIds, [], "分析启动失败的文件不得保留在待上传列表（会重复上传）");
assert.match(String(mixedAnalyze.summaryText), /上传成功但分析启动失败 1 个文件/);
assert.match(String(mixedAnalyze.summaryText), /job-2|决算B|分析启动失败/);
assert.ok(!String(mixedAnalyze.summaryText).includes("上传失败 1 个文件"), "REGRESSION: 分析启动失败不得被合并表述成'上传失败'");

// 上传失败：保留条目待重试
const uploadFailed: SubmitFileOutcome = {
  entryId: "e3",
  filename: "预算C.pdf",
  uploadOk: false,
  analysisStarted: false,
  jobId: null,
  failureText: "文件 预算C.pdf 上传失败（HTTP 413）",
};
const mixedUpload = summarizeSubmitOutcome([okOutcome, uploadFailed]);
assert.equal(mixedUpload.allSucceeded, false);
assert.deepEqual(mixedUpload.uploadedJobIds, ["job-1"], "上传失败的文件不得计入 uploadedJobIds");
assert.deepEqual(mixedUpload.failedEntryIds, ["e3"], "上传失败的条目必须保留（failedEntryIds）供重试");
assert.match(String(mixedUpload.summaryText), /上传失败 1 个文件/);
assert.match(String(mixedUpload.summaryText), /未创建任务/);
assert.ok(!String(mixedUpload.summaryText).includes("分析启动失败"), "上传失败不得被表述成分析启动失败");

// 混合三类：逐文件独立，互不影响
const mixedAll = summarizeSubmitOutcome([okOutcome, analyzeFailed, uploadFailed]);
assert.deepEqual(mixedAll.uploadedJobIds, ["job-1", "job-2"]);
assert.deepEqual(mixedAll.failedEntryIds, ["e3"]);
assert.match(String(mixedAll.summaryText), /上传成功但分析启动失败 1 个文件/);
assert.match(String(mixedAll.summaryText), /上传失败 1 个文件/);

// 空数组（理论上不应发生，但纯函数必须稳健）
const empty = summarizeSubmitOutcome([]);
assert.equal(empty.allSucceeded, true);
assert.equal(empty.summaryText, null);

console.log("uploadCenterAdapters.test.ts passed");
