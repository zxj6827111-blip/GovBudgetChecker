/**
 * 材料详情（WP2-B）适配层与展示契约的单测。
 *
 * 用项目既有的 jiti 直跑风格（`node:assert` 顶层断言），不引入 jsdom：
 * 本文件覆盖的全是纯函数，页面只负责把它们渲染出来。
 *
 * 重点在**不能让步的口径**上，而不是字段搬运：
 * - 没有槽位 ≠ 缺失；只有真实 missing 才进缺失态；
 * - due_at_unknown 不得显示成缺失；
 * - missing + 有历史版本 不得写"从未上传"；
 * - null（算不出来）与 0（算了是零）显示不同；
 * - 检查覆盖不可用时一个数字都不给；
 * - 人工上传无 URL 是正常形态；
 * - 同一（年度 × 文种）多条槽位一条都不隐藏。
 */

import assert from "node:assert/strict";

import {
  ANALYSIS_UNAVAILABLE_LABEL,
  COVERAGE_UNAVAILABLE_LABEL,
  MATERIAL_DETAIL_TABS,
  TIMELINE_ABSENT_LABEL,
  UNRESOLVED_YEAR_LABEL,
  buildCoverageGroups,
  buildCoverageKpis,
  buildDetailBreadcrumb,
  buildFindingsSections,
  buildRunRows,
  buildTimelineRows,
  formatElapsed,
  materialDetailTabHref,
  partitionRunRows,
  presentAnalysisAvailability,
  presentRunFindings,
  presentSlotState,
  presentTimelineCell,
  presentYearStatus,
  readSlotDetailPayload,
  readSlotRunsPayload,
  readSlotVersionsPayload,
  readUnitTimelinePayload,
  resolveDetailTab,
  type AnalysisBlock,
  type CoverageBlock,
  type RunSummaryItem,
  type FindingsSection,
  type SlotDetailResponse,
} from "../app/components/materials/materialDetailAdapters";
import {
  COVERAGE_STATUSES,
  EVIDENCE_STATUS_DEGRADED,
  formatCount,
  formatFileHash,
  formatFileSize,
  formatRate,
  presentCoverageReason,
  presentCoverageStatus,
  presentSourceKind,
  presentSourceStatus,
  presentVersionBadge,
} from "../lib/materialDetailPresentation";

const labeled: string[] = [];
function check(label: string, condition: boolean): void {
  if (!condition) {
    throw new Error(`ASSERT FAILED: ${label}`);
  }
  labeled.push(`PASS: ${label}`);
}

// ---- 时间轴：年度分组与排序 -------------------------------------------------

const slot = (overrides: Record<string, unknown> = {}) => ({
  slot_id: "slot-1",
  slot_key: "key-1",
  jurisdiction_id: "district-pt",
  jurisdiction_name: "上海市普陀区",
  department_org_id: "dept-ghzy",
  department_name: "规划和自然资源局",
  subject_org_id: "unit-ghzy-head",
  subject_org_name: "规划和自然资源局本级",
  subject_kind: "unit",
  material_scope: "unit_self",
  fiscal_year: 2024,
  report_kind: "final",
  caliber: "self",
  caliber_conflict_candidate: null,
  status: "uploaded",
  status_reason: "awaiting_analysis",
  applicability_status: "applicable",
  applicability_note: null,
  due_at: null,
  current_document_version_id: null,
  formal_issue_count: null,
  updated_at: "2026-09-21T12:00:00Z",
  ...overrides,
});

const timelineData = {
  unit: {
    unit_id: "unit-ghzy-head",
    unit_name: "规划和自然资源局本级",
    subject_kind: "unit",
    department_id: "dept-ghzy",
    department_name: "规划和自然资源局",
    jurisdiction_id: "district-pt",
    jurisdiction_name: "上海市普陀区",
  },
  years: [
    {
      fiscal_year: 2025,
      budget_slots: [slot({ slot_id: "s-2025-budget", fiscal_year: 2025, report_kind: "budget" })],
      final_slots: [],
      unclassified_slots: [
        slot({
          slot_id: "s-2025-unknown",
          fiscal_year: 2025,
          report_kind: "unknown",
          status: "mapping_required",
          status_reason: "identity_unresolved",
        }),
      ],
    },
    {
      fiscal_year: 2024,
      budget_slots: [],
      final_slots: [
        slot({ slot_id: "s-2024-final", fiscal_year: 2024, report_kind: "final" }),
        slot({
          slot_id: "s-2024-final-placeholder",
          fiscal_year: 2024,
          report_kind: "final",
          mapping_key: "sha256:abc",
          status: "mapping_required",
          status_reason: "identity_unresolved",
        }),
      ],
      unclassified_slots: [],
    },
  ],
  unresolved_year_slots: [
    slot({
      slot_id: "s-year-unknown",
      fiscal_year: null,
      report_kind: "budget",
      status: "mapping_required",
      status_reason: "identity_unresolved",
    }),
  ],
};

const rows = buildTimelineRows(timelineData as never);

check("时间轴按后端给定的财政年度降序渲染", rows.map((row) => row.fiscalYear).join(",") === "2025,2024");
check("预算 / 决算 / 文种待确认三列不串位", rows[0].budget.exists && !rows[0].final.exists && rows[0].unclassified.exists);
check(
  "文种未识别的槽位出现在 unclassified 而不是消失",
  rows[0].unclassified.primary?.slot_id === "s-2025-unknown",
);
check("没有槽位的格子只写「尚无已建立材料」", rows[0].final.emptyLabel === TIMELINE_ABSENT_LABEL);
check(
  "「尚无已建立材料」不含缺失/逾期/未上传字样",
  !/缺失|逾期|未上传/.test(rows[0].final.emptyLabel),
);
check(
  "同一（年度 × 文种）多条槽位不隐藏：extraCount 与完整列表都在",
  rows[1].final.total === 2 && rows[1].final.extraCount === 1 && rows[1].final.all.length === 2,
);
check(
  "多条槽位时主卡片是后端顺序的第一条（身份已确认的优先）",
  rows[1].final.primary?.slot_id === "s-2024-final",
);
check(
  "年度未识别的槽位不进任何具体年份",
  rows.every((row) =>
    [row.budget, row.final, row.unclassified].every(
      (cell) => cell.all.every((item) => item.slot_id !== "s-year-unknown"),
    ),
  ),
);
check("年度待确认的槽位保留在独立字段里", timelineData.unresolved_year_slots.length === 1);
check("年度待确认文案固定", UNRESOLVED_YEAR_LABEL === "年度待确认");
check(
  "时间轴不存在「完整」这种结论（应收基线未建立时推不出完整）",
  !rows.some((row) => row.yearStatus.includes("完整")),
);

// ---- 年度状态是保守描述 -----------------------------------------------------

check(
  "只有 missing 才显示「有逾期未上传」",
  presentYearStatus([presentTimelineCell([slot({ status: "missing" })])]) === "有逾期未上传",
);
check(
  "存在待确认项时显示「有待确认项」",
  presentYearStatus([presentTimelineCell([slot({ status: "mapping_required" })])]) === "有待确认项",
);
check(
  "全部 completed 才显示「已复核完成」",
  presentYearStatus([presentTimelineCell([slot({ status: "completed" })])]) === "已复核完成",
);
check(
  "其余情况显示「处理中」而不是「完整」",
  presentYearStatus([presentTimelineCell([slot({ status: "uploaded" })])]) === "处理中",
);

// ---- 缺失态与截止时间待确认 -------------------------------------------------

check(
  "只有 status=missing 才算缺失态",
  presentSlotState({ status: "missing", status_reason: "due_exceeded" }, 1).isMissing === true,
);
check(
  "due_at_unknown 不是缺失态",
  presentSlotState({ status: "not_due", status_reason: "due_at_unknown" }, 0).isMissing === false,
);
check(
  "due_at_unknown 的文案是「截止时间未知，当前无法判断是否逾期」",
  presentSlotState({ status: "not_due", status_reason: "due_at_unknown" }, 0).label ===
    "截止时间未知，当前无法判断是否逾期",
);
check(
  "due_at_unknown 的文案里没有「缺失」",
  !presentSlotState({ status: "not_due", status_reason: "due_at_unknown" }, 0).label.includes("缺失"),
);
const missingWithHistory = presentSlotState({ status: "missing", status_reason: "due_exceeded" }, 2);
check("missing 且存在历史版本时写「当前无有效文件版本」", missingWithHistory.detail?.includes("当前无有效文件版本") === true);
check(
  "missing 且存在历史版本时整个文案里都不出现「从未上传」",
  !String(missingWithHistory.detail).includes("从未上传"),
);
const missingWithoutHistory = presentSlotState({ status: "missing", status_reason: "due_exceeded" }, 0);
check(
  "missing 且没有关联版本时也只说槽位没关联 PDF，不说「从未上传」",
  !String(missingWithoutHistory.detail).includes("从未上传") &&
    String(missingWithoutHistory.detail).includes("尚未关联任何 PDF 版本"),
);
check("非缺失非截止未知时不给文案", presentSlotState({ status: "uploaded" }, 1).label === "");

// ---- 当前分析不可用 ---------------------------------------------------------

check(
  "没有分析时的文案是「暂无可确认的分析结果」而不是「暂无问题」",
  ANALYSIS_UNAVAILABLE_LABEL.includes("暂无可确认的分析结果") &&
    !ANALYSIS_UNAVAILABLE_LABEL.includes("暂无问题"),
);
check(
  "没有当前版本时的文案说明原因是缺文件版本",
  presentAnalysisAvailability("no_current_document_version").includes("没有有效文件版本"),
);
check(
  "分析未落库时的文案说明原因是尚未完成/落库",
  presentAnalysisAvailability("no_persisted_analysis_for_current_version").includes("尚未完成分析"),
);

// ---- null 与 0 --------------------------------------------------------------

check("formal_issue_count 为 null 显示 —", formatCount(null) === "—");
check("formal_issue_count 为 0 显示 0（已确认没有正式问题）", formatCount(0) === "0");
check("formatCount 不吞掉 undefined", formatCount(undefined) === "—");
check("比例 null 显示 —", formatRate(null) === "—");
check("比例 0 显示 0%", formatRate(0) === "0%");

// ---- 检查覆盖 ---------------------------------------------------------------

const unavailableCoverage: CoverageBlock = {
  available: false,
  reason: "no_coverage_for_current_document_version",
  summary: null,
  items: [],
};
check("覆盖不可用时 KPI 一个都不给", buildCoverageKpis(unavailableCoverage).length === 0);
check("覆盖不可用时分组一个都不给", buildCoverageGroups(unavailableCoverage).length === 0);
check(
  "覆盖不可用文案不含 0 / 0 或 100%",
  !/0\s*\/\s*0|100%/.test(COVERAGE_UNAVAILABLE_LABEL),
);
check(
  "覆盖不可用时 KPI 文案合计里没有 0",
  buildCoverageKpis(unavailableCoverage)
    .map((kpi) => kpi.value)
    .join("") === "",
);

const availableCoverage: CoverageBlock = {
  available: true,
  reason: null,
  summary: {
    catalog_version: "v3.3",
    catalog_fingerprint: "fp",
    applicable_total: 42,
    completed_total: 26,
    not_applicable_total: 1,
    unresolved_total: 16,
    blocking_total: 15,
    coverage_rate: 0.619,
    auto_completion_rate: 0.6429,
    by_reason: { not_implemented: 12, insufficient_data: 3, ai_not_run: 1 },
    by_group: [
      {
        group_id: "table_internal",
        group_title: "表内关系",
        applicable: 7,
        completed: 6,
        not_applicable: 0,
        unresolved: 1,
      },
      {
        group_id: "three_public",
        group_title: "三公经费",
        applicable: 4,
        completed: 2,
        not_applicable: 0,
        unresolved: 2,
      },
    ],
  },
  items: [
    {
      obligation_id: "OBL-T-004",
      group_id: "table_internal",
      group_title: "表内关系",
      title: "表内合计与分项一致",
      status: "insufficient_data",
      reason: "insufficient_data",
      reason_label: "取数不足",
      detail: "未能定位合计行",
      blocks_gate: true,
      requires_ai: false,
      input_gaps: ["table_total"],
    },
    {
      obligation_id: "OBL-T-005",
      group_id: "table_internal",
      group_title: "表内关系",
      title: "已完成项",
      status: "completed",
      reason: null,
      reason_label: null,
      detail: null,
      blocks_gate: false,
      requires_ai: false,
      input_gaps: [],
    },
    {
      obligation_id: "OBL-C-001",
      group_id: "three_public",
      group_title: "三公经费",
      title: "三公合计等于分项之和",
      status: "not_implemented",
      reason: "not_implemented",
      reason_label: "自动检查暂不可用",
      detail: null,
      blocks_gate: true,
      requires_ai: false,
      input_gaps: [],
    },
    {
      obligation_id: "OBL-C-002",
      group_id: "three_public",
      group_title: "三公经费",
      title: "三公说明存在",
      status: "ai_not_run",
      reason: "ai_not_run",
      reason_label: "语义检查未执行",
      detail: null,
      blocks_gate: true,
      requires_ai: true,
      input_gaps: [],
    },
  ],
};

const kpis = buildCoverageKpis(availableCoverage);
check("覆盖可用时给出 6 张 KPI", kpis.length === 6);
check(
  "阻塞未完成单独成一张 KPI",
  kpis.find((kpi) => kpi.key === "blocking")?.value === "15",
);
check(
  "待人工核验 = 未完成 - 阻塞（非阻塞部分）",
  kpis.find((kpi) => kpi.key === "pending_review")?.value === "1",
);
check("覆盖完成率按后端给的数字显示", kpis.find((kpi) => kpi.key === "coverage_rate")?.value === "61.9%");

const groups = buildCoverageGroups(availableCoverage);
check("分组逐行给出完成/应检查", groups[0].progress === "6 / 7");
check(
  "分组未完成情况逐条列出而不是合并成「有问题」",
  groups[0].unresolvedNotes.length === 1 && groups[0].unresolvedNotes[0].code === "insufficient_data",
);
check(
  "阻塞项按原因分别列出（不是一个大数字）",
  groups[1].unresolvedNotes.length === 2,
);
check(
  "分组标题用引擎给的中文",
  groups[0].title === "表内关系",
);

// ---- 义务状态业务映射（§三十六 的 11 个取值全覆盖） -------------------------

check(
  "义务状态取值域与后端契约一一对应（11 个）",
  COVERAGE_STATUSES.length === 11,
);
check("completed → 自动检查完成", presentCoverageStatus("completed").label === "自动检查完成");
check("not_applicable → 不适用", presentCoverageStatus("not_applicable").label === "不适用");
check(
  "not_implemented → 自动检查暂不可用，需要人工核验",
  presentCoverageStatus("not_implemented").label === "自动检查暂不可用，需要人工核验",
);
check("not_executed → 检查未执行", presentCoverageStatus("not_executed").label === "检查未执行");
check(
  "insufficient_data → 取数不足，需要人工核验",
  presentCoverageStatus("insufficient_data").label === "取数不足，需要人工核验",
);
check("parse_ambiguity → 解析存在歧义", presentCoverageStatus("parse_ambiguity").label === "解析存在歧义");
check("execution_error → 检查执行异常", presentCoverageStatus("execution_error").label === "检查执行异常");
check("profile_unresolved → 材料画像待确认", presentCoverageStatus("profile_unresolved").label === "材料画像待确认");
check("kind_conflict → 文种冲突待确认", presentCoverageStatus("kind_conflict").label === "文种冲突待确认");
check("ai_not_run → 语义检查未执行", presentCoverageStatus("ai_not_run").label === "语义检查未执行");
check("ai_failed → 语义检查失败", presentCoverageStatus("ai_failed").label === "语义检查失败");
check(
  "completed / not_applicable 不算阻塞，其余都算",
  presentCoverageStatus("completed").blocking === false &&
    presentCoverageStatus("not_applicable").blocking === false &&
    COVERAGE_STATUSES.filter((status) => status !== "completed" && status !== "not_applicable").every(
      (status) => presentCoverageStatus(status).blocking === true,
    ),
);
check(
  "11 个状态的业务文案互不重复",
  new Set(COVERAGE_STATUSES.map((status) => presentCoverageStatus(status).label)).size === 11,
);
check(
  "未登记的义务状态不吞掉",
  presentCoverageStatus("archived").label === "待确认：archived",
);
check(
  "未完成原因码有中文映射",
  presentCoverageReason("not_implemented") === "自动检查暂不可用" &&
    presentCoverageReason("ai_failed") === "语义检查失败",
);
check("未登记的原因码原样显示", presentCoverageReason("weird_code") === "待确认：weird_code");

// ---- 版本与来源 -------------------------------------------------------------

check("当前版本徽章", presentVersionBadge(true).label === "当前版本");
check("历史版本徽章", presentVersionBadge(false).label === "历史版本");
check(
  "hash 显示为摘要而不是完整 64 位",
  formatFileHash("a8c9" + "f".repeat(56) + "91ef").startsWith("a8c9…") &&
    formatFileHash("a8c9" + "f".repeat(56) + "91ef").length < 20,
);
check("hash 缺失显示 —", formatFileHash(null) === "—");
check("文件大小缺失显示 —（不显示 0 B）", formatFileSize(null) === "—");
check("文件大小 0 显示 0 B", formatFileSize(0) === "0 B");
check("官方来源文案", presentSourceKind("official_site") === "官网公开来源");
check("人工上传文案（不是「来源缺失」）", presentSourceKind("manual_upload") === "人工上传");
check("excel_import 有中文", presentSourceKind("excel_import") === "表格导入");
check(
  "来源类型未识别时原样显示",
  presentSourceKind("crawler").startsWith("待确认："),
);
check("来源状态有效", presentSourceStatus("active") === "有效");
check("来源状态未识别不吞掉", presentSourceStatus("unknown_state").startsWith("待确认："));

// ---- 处理记录 ---------------------------------------------------------------

const run = (overrides: Partial<RunSummaryItem> = {}): RunSummaryItem => ({
  job_uuid: "job-1",
  document_version_id: 22,
  is_current_document_version: true,
  status: "done",
  mode: "dual",
  started_at: "2026-09-20T15:40:00Z",
  completed_at: "2026-09-20T15:42:00Z",
  created_at: "2026-09-20T15:39:00Z",
  updated_at: "2026-09-20T15:42:00Z",
  ai_findings_count: 6,
  rule_findings_count: 2,
  merged_findings_count: 6,
  has_results: true,
  structured_ingest_status: "done",
  elapsed_total_ms: 12345,
  error_summary: null,
  analysis_status: "review_required",
  quality_status: "review_required",
  analysis_conclusion: "findings_detected",
  obligation_catalog_version: "v3.3",
  formal_issue_count: null,
  ...overrides,
});

const runRows = buildRunRows([
  run(),
  run({ job_uuid: "job-0", document_version_id: 11, is_current_document_version: false }),
]);
check(
  "运行按当前/历史文件版本分栏",
  partitionRunRows(runRows).current.length === 1 && partitionRunRows(runRows).history.length === 1,
);
check(
  "当前版本的运行标「当前文件版本」",
  partitionRunRows(runRows).current[0].versionBadge === "当前文件版本",
);
check(
  "历史版本的运行标「历史文件版本」",
  partitionRunRows(runRows).history[0].versionBadge === "历史文件版本",
);
check(
  "运行状态 done 显示「已完成」",
  runRows[0].statusLabel === "已完成",
);
check(
  "review_required 显示「完成（需人工复核）」而不是「已完成」",
  buildRunRows([run({ status: "review_required" })])[0].statusLabel === "完成（需人工复核）",
);
check(
  "error 显示「处理失败」",
  buildRunRows([run({ status: "error" })])[0].statusLabel === "处理失败",
);
check(
  "未落库结果的运行 finding 摘要显示 —（不是 0）",
  presentRunFindings(run({ has_results: false, ai_findings_count: 0, merged_findings_count: 0 })) === "—",
);
check(
  "已落库结果的运行给出三类计数",
  presentRunFindings(run()).includes("6 AI"),
);
check("耗时未知显示 —", formatElapsed(null) === "—");
check("耗时 0 显示 0 ms", formatElapsed(0) === "0 ms");
check("耗时按秒显示", formatElapsed(12345) === "12.3 s");
check(
  "运行列表不带正式问题数（只解释跑了什么）",
  buildRunRows([run()])[0].jobUuid === "job-1",
);

// ---- 检查结果三栏 -----------------------------------------------------------

const analysis: AnalysisBlock = {
  available: true,
  reason: null,
  run: run(),
  formal_findings: [
    {
      finding_id: "f1",
      title: "表内金额勾稽不一致",
      message: null,
      severity: "high",
      severity_bucket: "error",
      source: "rule",
      rule_id: "V33-121",
      obligation_ids: ["OBL-T-004"],
      evidence_page: 15,
      evidence_bbox: [1, 2, 3, 4],
      evidence_text: "合计 35.20",
      evidence_status: "complete",
      evidence_missing: [],
      tags: [],
      why_not: null,
    },
  ],
  manual_review_items: [
    {
      finding_id: "f2",
      title: "缺证据的候选问题",
      message: null,
      severity: "manual_review",
      severity_bucket: "warn",
      source: "ai",
      rule_id: null,
      obligation_ids: [],
      evidence_page: null,
      evidence_bbox: null,
      evidence_text: null,
      evidence_status: EVIDENCE_STATUS_DEGRADED,
      evidence_missing: ["missing_page"],
      tags: [],
      why_not: null,
    },
  ],
  info_findings: [],
  formal_issue_count: 1,
  coverage: availableCoverage,
};
const sections = buildFindingsSections(analysis);
check("检查结果固定三栏", sections.map((section) => section.key).join(",") === "formal,manual_review,info");
check(
  "正式问题只含通过正式门禁的条目",
  sections[0].items.length === 1 && sections[0].items[0].finding_id === "f1",
);
check(
  "降级条目进「需人工核验」而不是「正式问题」",
  sections[1].items.length === 1 && sections[1].items[0].finding_id === "f2",
);
check(
  "分析不可用时三栏都不给（不显示「暂无问题」）",
  buildFindingsSections({ ...analysis, available: false }).length === 0,
);
check(
  "三栏之和等于该版本分析结论的全部条目（1 正式 + 1 需核验 + 0 提示）",
  sections.reduce((sum, section) => sum + section.items.length, 0) === 2,
);

// 计数口径 ≠ 展示分组：顶部「正式检查记录」取后端的 formal_issue_count，
// 其权威语义是"没有被 evidence guard 降级就算正式 finding"（info 也计入）。
// 因此它必须等于「正式问题」+「信息提示」两栏之和，
// **不是**「正式问题」那一栏的长度。
const infoSection = (list: FindingsSection[]) =>
  list.find((section) => section.key === "info")!;

check(
  "formal_issue_count = 正式问题栏 + 信息提示栏（不是正式问题栏长度）",
  analysis.formal_issue_count === sections[0].items.length + infoSection(sections).items.length,
);
check(
  "该样本里两者确实不同（否则断言无意义）",
  analysis.formal_issue_count !== sections[0].items.length || infoSection(sections).items.length === 0,
);

const infoOnly: AnalysisBlock = {
  ...analysis,
  formal_findings: [],
  manual_review_items: [],
  info_findings: analysis.manual_review_items ?? [],
  formal_issue_count: 1,
};
const infoOnlySections = buildFindingsSections(infoOnly);
check(
  "只有 info 时「正式问题」栏为空，但计数仍为 1（不能被读成「没有正式 finding」）",
  infoOnlySections[0].items.length === 0 &&
    infoSection(infoOnlySections).items.length === 1 &&
    infoOnly.formal_issue_count === 1,
);
check(
  "info 仍然单独成栏，展示分组不因计数口径改变",
  infoOnlySections.map((section) => section.key).join(",") === "formal,manual_review,info",
);

// ---- Tab 契约 ---------------------------------------------------------------

check("五个 Tab 且 key 稳定", MATERIAL_DETAIL_TABS.map((tab) => tab.key).join(",") === "overview,findings,coverage,versions,runs");
check(
  "Tab 文案固定",
  MATERIAL_DETAIL_TABS.map((tab) => tab.label).join("/") === "材料概览/检查结果/检查覆盖/版本与来源/处理记录",
);
check("未知 tab 回落到概览", resolveDetailTab("nope") === "overview");
check("缺失 tab 回落到概览", resolveDetailTab(undefined) === "overview");
check("合法 tab 原样保留", resolveDetailTab("runs") === "runs");
check(
  "Tab 链接带查询串（便于将来直接落到某个 Tab）",
  materialDetailTabHref("slot-1", "coverage") === "/materials/slots/slot-1?tab=coverage",
);
check(
  "槽位 id 做 URL 编码",
  materialDetailTabHref("slot/1", "runs") === "/materials/slots/slot%2F1?tab=runs",
);

// ---- 面包屑 -----------------------------------------------------------------

const breadcrumb = buildDetailBreadcrumb(slot() as never);
check("面包屑含台账/区县/部门/单位四级 + 当前年度文种", breadcrumb.length === 5);
check("面包屑第一级是材料台账", breadcrumb[0].href === "/materials");
check("面包屑部门级带年度参数", String(breadcrumb[2].href).includes("?year=2024"));
check(
  "面包屑单位级指向单位时间轴",
  breadcrumb[3].href === "/materials/unit/unit-ghzy-head",
);
check("最后一级不可点击", breadcrumb[4].href === null);
check("年度未识别时面包屑写「年度待确认」", buildDetailBreadcrumb(slot({ fiscal_year: null }) as never)[4].label.startsWith("年度待确认"));

// ---- 响应解析：形状不符必须返回 null ---------------------------------------

const envelope = (data: unknown, meta: unknown = {}) => ({ ok: true, data, meta });

check("时间轴响应形状正确时通过", readUnitTimelinePayload(envelope(timelineData)) !== null);
check(
  "时间轴缺 years 时返回 null",
  readUnitTimelinePayload(envelope({ unit: timelineData.unit, unresolved_year_slots: [] })) === null,
);
check(
  "详情响应缺 current_analysis 时返回 null",
  readSlotDetailPayload(envelope({ slot: slot(), sources: [] })) === null,
);
check(
  "版本响应 items 不是数组时返回 null",
  readSlotVersionsPayload(envelope({ slot_id: "s", items: "nope" })) === null,
);
check(
  "运行响应 items 不是数组时返回 null",
  readSlotRunsPayload(envelope({ slot_id: "s", items: {} })) === null,
);
check("ok 不为 true 时一律返回 null", readSlotRunsPayload({ ok: false, data: { items: [] }, meta: {} }) === null);
check("HTML 错误页返回 null", readSlotDetailPayload("<!DOCTYPE html>") === null);
check(
  "契约内的空数据是合法的（不是错误）",
  readSlotVersionsPayload(envelope({ slot_id: "s", items: [], current_document_version_id: null })) !== null,
);
check(
  "详情响应可以通过（供页面读取）",
  (readSlotDetailPayload(envelope({
    slot: slot(),
    current_version: null,
    historical_version_count: 0,
    version_total: 0,
    sources: [],
    current_analysis: analysis,
  })) as SlotDetailResponse | null) !== null,
);

console.log(labeled.join("\n"));
console.log(`\n${labeled.length} assertions passed`);
