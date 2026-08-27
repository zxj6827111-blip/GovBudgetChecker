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

console.log("uiAdapters.status.test.ts passed");
