import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import {
  DEFAULT_QUEUE_FILTERS,
} from "../app/components/queue/queuePageAdapters";
import {
  buildReportDownloadUrl,
  countTerminalJobs,
  filterHistoryJobs,
} from "../app/components/history/historyPageAdapters";

// 本文件测试 Task 8.2 任务历史页的纯逻辑层：终态过滤（completed/
// review_required/failed，复用 isUiTaskFinished 同源口径）与报告下载 URL 构造。

const mixedJobs: JobSummaryRecord[] = [
  { job_id: "j-done", filename: "a.pdf", status: "done", report_kind: "budget" },
  { job_id: "j-review", filename: "b.pdf", status: "review_required", report_kind: "unknown" },
  { job_id: "j-error", filename: "c.pdf", status: "error", report_kind: "budget" },
  { job_id: "j-processing", filename: "d.pdf", status: "processing", report_kind: "budget" },
  { job_id: "j-queued", filename: "e.pdf", status: "queued", report_kind: "budget" },
];

// --- filterHistoryJobs：只保留终态任务 ---------------------------------------------

assert.deepEqual(
  filterHistoryJobs(mixedJobs, DEFAULT_QUEUE_FILTERS).map((job) => job.job_id),
  ["j-done", "j-review", "j-error"],
  "终态口径 = completed/review_required/failed（isUiTaskFinished 同源）；" +
    "processing/queued 还在跑，不在任务历史",
);

assert.deepEqual(
  filterHistoryJobs(null, DEFAULT_QUEUE_FILTERS),
  [],
  "未拉到数据时返回空数组",
);

// 终态过滤 × 多维筛选（复用处理队列页的六维筛选）
assert.deepEqual(
  filterHistoryJobs(mixedJobs, { ...DEFAULT_QUEUE_FILTERS, status: "review_required" }).map(
    (job) => job.job_id,
  ),
  ["j-review"],
  "终态 + 状态筛选组合",
);
assert.deepEqual(
  filterHistoryJobs(mixedJobs, { ...DEFAULT_QUEUE_FILTERS, keyword: "c.pdf" }).map(
    (job) => job.job_id,
  ),
  ["j-error"],
  "终态 + 关键词筛选组合",
);
assert.deepEqual(
  filterHistoryJobs(mixedJobs, { ...DEFAULT_QUEUE_FILTERS, status: "analyzing" }),
  [],
  "analyzing 筛选在历史页永远为空（非终态已先被排除），状态选项里也不该有它",
);

assert.equal(countTerminalJobs(mixedJobs), 3);
assert.equal(countTerminalJobs(null), 0);
assert.equal(countTerminalJobs([]), 0, "空列表是真实的 0");

// --- buildReportDownloadUrl：接既有 /api/reports/download ---------------------------

assert.equal(
  buildReportDownloadUrl("job-001", "pdf"),
  "/api/reports/download?job_id=job-001&format=pdf",
);
assert.equal(
  buildReportDownloadUrl("job-001", "csv"),
  "/api/reports/download?job_id=job-001&format=csv",
);
assert.equal(
  buildReportDownloadUrl("job-001", "json"),
  "/api/reports/download?job_id=job-001&format=json",
);
// jobId 含特殊字符时必须编码，防止拼接出可被篡改的查询串
assert.equal(
  buildReportDownloadUrl("job/001?a=1", "pdf"),
  "/api/reports/download?job_id=job%2F001%3Fa%3D1&format=pdf",
  "jobId 必须经 encodeURIComponent 编码",
);

console.log("historyPageAdapters.test.ts: all assertions passed");
