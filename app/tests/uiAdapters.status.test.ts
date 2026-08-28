import assert from "node:assert/strict";

import {
  extractReviewReasonMessages,
  getUiTaskStatusMeta,
  isUiTaskFinished,
  normalizeAnalysisConclusion,
  normalizeUiQualityStatus,
  normalizeUiTaskStatus,
  resolveReportLabel,
  toUiTask,
} from "../lib/uiAdapters";
import type { JobSummaryRecord } from "../lib/uiAdapters";

// --- normalizeUiTaskStatus：四态归一，且旧任务不能被破坏 ---------------------

assert.equal(
  normalizeUiTaskStatus("review_required"),
  "review_required",
  "review_required must be an independent state, not folded into completed/failed",
);
assert.equal(normalizeUiTaskStatus("needs_review"), "review_required");
assert.equal(normalizeUiTaskStatus("  Review  "), "review_required");

// 向后兼容：历史任务的 done / degraded / completed 仍然展示为已完成
assert.equal(normalizeUiTaskStatus("done"), "completed", "legacy done must stay completed");
assert.equal(normalizeUiTaskStatus("degraded"), "completed", "degraded keeps a valid conclusion");
assert.equal(normalizeUiTaskStatus("completed"), "completed");
assert.equal(normalizeUiTaskStatus("SUCCESS"), "completed");

assert.equal(normalizeUiTaskStatus("error"), "failed");
assert.equal(normalizeUiTaskStatus("failed"), "failed");
assert.equal(normalizeUiTaskStatus("cancelled"), "failed");

assert.equal(normalizeUiTaskStatus("queued"), "analyzing");
assert.equal(normalizeUiTaskStatus("processing"), "analyzing");
assert.equal(normalizeUiTaskStatus(undefined), "analyzing");
assert.equal(normalizeUiTaskStatus("something-unknown"), "analyzing");

// --- isUiTaskFinished：review_required 是终态，不能显示成"还在跑" -------------

assert.equal(isUiTaskFinished("review_required"), true);
assert.equal(isUiTaskFinished("completed"), true);
assert.equal(isUiTaskFinished("failed"), true);
assert.equal(isUiTaskFinished("analyzing"), false);

// --- getUiTaskStatusMeta：文案与色调 ----------------------------------------

assert.deepEqual(getUiTaskStatusMeta({ status: "review_required" }), {
  status: "review_required",
  label: "需人工复核",
  tone: "orange",
});
assert.deepEqual(getUiTaskStatusMeta({ status: "done" }), {
  status: "completed",
  label: "已完成",
  tone: "green",
});
assert.deepEqual(
  getUiTaskStatusMeta({ status: "degraded", quality_status: "degraded" }),
  { status: "completed", label: "完成（部分降级）", tone: "orange" },
  "degraded must be visibly marked instead of silently shown as 已完成",
);
assert.deepEqual(getUiTaskStatusMeta({ status: "error" }), {
  status: "failed",
  label: "失败",
  tone: "red",
});
// 旧任务没有 quality_status 字段时不能报错，按普通完成展示
assert.equal(getUiTaskStatusMeta({ status: "done", quality_status: null }).label, "已完成");

// --- 质量字段归一 -----------------------------------------------------------

assert.equal(normalizeUiQualityStatus("degraded"), "degraded");
assert.equal(normalizeUiQualityStatus("review_required"), "review_required");
assert.equal(normalizeUiQualityStatus("garbage"), undefined);
assert.equal(normalizeUiQualityStatus(undefined), undefined);

assert.equal(normalizeAnalysisConclusion("no_findings"), "no_findings");
assert.equal(normalizeAnalysisConclusion("incomplete"), "incomplete");
assert.equal(normalizeAnalysisConclusion("nope"), undefined);

assert.deepEqual(
  extractReviewReasonMessages([
    { code: "scanned_pages_detected", message: "检测到 2 页疑似扫描页" },
    { code: "unknown_report_year" },
    "原样字符串",
    null,
    123,
  ]),
  ["检测到 2 页疑似扫描页", "unknown_report_year", "原样字符串"],
);
assert.deepEqual(extractReviewReasonMessages(undefined), []);

// --- resolveReportLabel：unknown 绝不能显示成"预算"（P1-05）-----------------

assert.equal(resolveReportLabel("budget", "department"), "部门预算");
assert.equal(resolveReportLabel("final", "unit"), "单位决算");
assert.equal(
  resolveReportLabel("unknown", "department"),
  "部门待复核",
  "unknown report kind must not be guessed as 预算",
);
assert.equal(resolveReportLabel(undefined, "unit"), "单位待复核");

// --- toUiTask：状态与质量字段贯通 -------------------------------------------

const reviewTask = toUiTask({
  job_id: "job-review",
  filename: "某单位公开材料.pdf",
  status: "review_required",
  quality_status: "review_required",
  analysis_conclusion: "incomplete",
  review_reasons: [
    { code: "scanned_pages_detected", message: "检测到 2 页疑似扫描页" },
    { code: "unknown_report_kind", message: "未能识别材料类型" },
  ],
  page_coverage: 0.5,
  scanned_page_count: 2,
  report_kind: "unknown",
  report_year: 2025,
});

assert.equal(reviewTask.status, "review_required");
assert.equal(reviewTask.qualityStatus, "review_required");
assert.equal(reviewTask.analysisConclusion, "incomplete");
assert.deepEqual(reviewTask.reviewReasons, ["检测到 2 页疑似扫描页", "未能识别材料类型"]);
// 文件名含"单位" -> inferReportSubjectType 推为 unit；关键是 unknown 没被猜成"预算"
assert.equal(reviewTask.reportLabel, "单位待复核");
// review_required 已跑完，流水线步骤不能显示成未开始
assert.equal(reviewTask.pipeline.parse, "done");

const degradedTask = toUiTask({
  job_id: "job-degraded",
  filename: "某局2025年部门预算.pdf",
  status: "degraded",
  quality_status: "degraded",
  analysis_conclusion: "findings_detected",
  report_kind: "budget",
  report_year: 2025,
});
assert.equal(degradedTask.status, "completed");
assert.equal(degradedTask.qualityStatus, "degraded");
assert.equal(degradedTask.reportLabel, "部门预算");

// 历史任务（无任何新字段）必须仍然可读，不能抛错也不能出现 undefined 状态
const legacyTask = toUiTask({
  job_id: "job-legacy",
  filename: "2024年某局部门决算.pdf",
  status: "done",
  report_kind: "final",
  report_year: 2024,
});
assert.equal(legacyTask.status, "completed");
assert.equal(legacyTask.qualityStatus, undefined);
assert.equal(legacyTask.analysisConclusion, undefined);
assert.deepEqual(legacyTask.reviewReasons, []);
assert.equal(legacyTask.reportLabel, "部门决算");



// ===========================================================================
// 修复 3：uploaded（已上传未分析）必须如实显示为"待分析"静止态
// ===========================================================================

// --- normalizeUiTaskStatus：uploaded 独立成态，严禁归一到 processing/done ----

assert.equal(
  normalizeUiTaskStatus("uploaded"),
  "pending_analysis",
  "REGRESSION: uploaded 必须归一为 pending_analysis（待分析），不得兜底成 analyzing（虚假进度）",
);
assert.equal(normalizeUiTaskStatus("  UPLOADED "), "pending_analysis");
assert.equal(normalizeUiTaskStatus("pending_analysis"), "pending_analysis");

// 核心反例（任务书修复 3 红线）：不得为了界面"好看"归一到 processing/done
assert.notEqual(normalizeUiTaskStatus("uploaded"), "analyzing");
assert.notEqual(normalizeUiTaskStatus("uploaded"), "completed");
assert.notEqual(normalizeUiTaskStatus("uploaded"), "review_required");
assert.notEqual(normalizeUiTaskStatus("uploaded"), "failed");

// --- isUiTaskFinished：待分析不是终态（不得进入"任务历史"等终态集合） -------

assert.equal(isUiTaskFinished("pending_analysis"), false);

// --- getUiTaskStatusMeta：状态文案对照表（表驱动） ---------------------------
// 每个后端状态 → 界面文案 → 语义一致性断言（任务书交付要求 4 的机器可读版）。

const statusMetaTable: Array<{
  raw: string;
  wantStatus: string;
  wantLabel: string;
  forbid: RegExp;
}> = [
  { raw: "uploaded", wantStatus: "pending_analysis", wantLabel: "待分析", forbid: /分析中|正在|处理中|执行中|完成/ },
  { raw: "queued", wantStatus: "analyzing", wantLabel: "执行中", forbid: /待分析|完成|失败/ },
  { raw: "processing", wantStatus: "analyzing", wantLabel: "执行中", forbid: /待分析|完成|失败/ },
  { raw: "done", wantStatus: "completed", wantLabel: "已完成", forbid: /分析中|待分析/ },
  { raw: "review_required", wantStatus: "review_required", wantLabel: "需人工复核", forbid: /分析完成|失败/ },
  { raw: "failed", wantStatus: "failed", wantLabel: "失败", forbid: /完成|分析中/ },
  { raw: "error", wantStatus: "failed", wantLabel: "失败", forbid: /完成|分析中/ },
];

for (const row of statusMetaTable) {
  const meta = getUiTaskStatusMeta({ status: row.raw });
  assert.equal(meta.status, row.wantStatus, `状态 ${row.raw} 归一结果`);
  assert.equal(meta.label, row.wantLabel, `状态 ${row.raw} 的界面文案`);
  assert.ok(
    !row.forbid.test(meta.label),
    `REGRESSION: 状态 ${row.raw} 的文案「${meta.label}」违反语义（禁配 ${row.forbid}）`,
  );
}

// 降级完成仍带降级标记，不得粉饰成纯粹的"已完成"
assert.equal(
  getUiTaskStatusMeta({ status: "done", quality_status: "degraded" }).label,
  "完成（部分降级）",
);

// --- toUiTask：uploaded 任务的流水线必须全部 pending（没有任何步骤在跑） ----

const uploadedJob: JobSummaryRecord = {
  job_id: "job-uploaded",
  filename: "2026年度部门预算.pdf",
  status: "uploaded",
  stage: "uploaded",
};
const uploadedTask = toUiTask(uploadedJob);
assert.equal(uploadedTask.status, "pending_analysis");
assert.deepEqual(
  uploadedTask.pipeline,
  { parse: "pending", extract: "pending", review: "pending", report: "pending" },
  "REGRESSION: 待分析任务的流水线不得显示任何 done/processing 步骤（那是虚假进度）",
);

console.log("uiAdapters.status.test.ts passed");
