import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import {
  DEFAULT_QUEUE_FILTERS,
  countAnalyzingJobs,
  filterQueueJobs,
  paginateJobs,
  sortQueueJobs,
} from "../app/components/queue/queuePageAdapters";

// 本文件测试 Task 8.1 处理队列全量页的纯逻辑层：多维筛选（复用工作台
// 四维 + 文档类型 + 阶段）、排序、分页、与侧边栏角标同口径的计数。

// --- filterQueueJobs：文档类型与阶段两维（叠加在工作台四维之上） ------------------

const sampleJobs: JobSummaryRecord[] = [
  {
    job_id: "j-budget",
    filename: "2026预算.pdf",
    status: "done",
    report_kind: "budget",
    report_year: 2026,
    organization_id: "org-a",
    stage_progress: { phase: "quality_gate", phase_label: "质量门禁", percent: 100, raw_stage: "完成" },
  },
  {
    job_id: "j-final",
    filename: "2025决算.pdf",
    status: "review_required",
    report_kind: "final",
    report_year: 2025,
    organization_id: "org-b",
    stage_progress: { phase: "quality_gate", phase_label: "质量门禁", percent: 100, raw_stage: "完成（需人工复核）" },
  },
  {
    job_id: "j-unknown-kind",
    filename: "扫描件.pdf",
    status: "review_required",
    report_kind: "unknown",
    report_year: null,
    organization_id: null,
    stage_progress: null,
  },
  {
    job_id: "j-processing",
    filename: "处理中.pdf",
    status: "processing",
    report_kind: "budget",
    report_year: 2026,
    organization_id: "org-a",
    stage_progress: { phase: "pdf_parse", phase_label: "PDF 解析", percent: 20, raw_stage: "解析PDF内容" },
  },
  {
    job_id: "j-legacy-stage",
    filename: "历史任务.pdf",
    status: "done",
    report_kind: "budget",
    report_year: 2026,
    organization_id: "org-a",
    stage_progress: null, // 历史任务：无阶段留痕 → 未知阶段
  },
];

assert.deepEqual(
  filterQueueJobs(sampleJobs, DEFAULT_QUEUE_FILTERS).map((job) => job.job_id),
  ["j-budget", "j-final", "j-unknown-kind", "j-processing", "j-legacy-stage"],
  "默认筛选（全部）返回全部任务",
);

assert.deepEqual(
  filterQueueJobs(sampleJobs, { ...DEFAULT_QUEUE_FILTERS, reportKind: "budget" }).map((job) => job.job_id),
  ["j-budget", "j-processing", "j-legacy-stage"],
  "文档类型筛选 budget",
);
assert.deepEqual(
  filterQueueJobs(sampleJobs, { ...DEFAULT_QUEUE_FILTERS, reportKind: "unknown" }).map((job) => job.job_id),
  ["j-unknown-kind"],
  "文档类型筛选 unknown（待复核）",
);

assert.deepEqual(
  filterQueueJobs(sampleJobs, { ...DEFAULT_QUEUE_FILTERS, stage: "pdf_parse" }).map((job) => job.job_id),
  ["j-processing"],
  "阶段筛选：当前处于 PDF 解析的任务",
);
assert.deepEqual(
  filterQueueJobs(sampleJobs, { ...DEFAULT_QUEUE_FILTERS, stage: "unknown" }).map((job) => job.job_id),
  ["j-unknown-kind", "j-legacy-stage"],
  "阶段筛选'未知阶段'：无 stage_progress 留痕的任务（历史任务），不与任何真实阶段混淆",
);

// 组合筛选：状态 × 类型
assert.deepEqual(
  filterQueueJobs(sampleJobs, {
    ...DEFAULT_QUEUE_FILTERS,
    status: "review_required",
    reportKind: "unknown",
  }).map((job) => job.job_id),
  ["j-unknown-kind"],
  "组合筛选：待复核 + 类型未识别",
);

// 反例：脏数据 phase（OCR 不存在）归入未知阶段，不渲染成独立分段
const dirtyStageJobs: JobSummaryRecord[] = [
  {
    job_id: "j-dirty",
    status: "processing",
    stage_progress: { phase: "ocr", phase_label: "OCR", percent: 10, raw_stage: "x" },
  },
];
assert.deepEqual(
  filterQueueJobs(dirtyStageJobs, { ...DEFAULT_QUEUE_FILTERS, stage: "unknown" }).map((job) => job.job_id),
  ["j-dirty"],
  "REGRESSION: 脏数据 phase=ocr 必须归入'未知阶段'（阶段枚举无 OCR）",
);

assert.deepEqual(filterQueueJobs(null, DEFAULT_QUEUE_FILTERS), [], "未拉到数据时返回空数组");

// --- sortQueueJobs ----------------------------------------------------------------

const sortJobs: JobSummaryRecord[] = [
  { job_id: "a", merged_issue_total: 3, elapsed_ms: 500 },
  { job_id: "b", merged_issue_total: 10, elapsed_ms: 2000 },
  { job_id: "c", issue_total: 7, elapsed_ms: null }, // legacy 字段兜底；耗时未知
  { job_id: "d", elapsed_ms: 100 }, // 无问题数 → 0
];

assert.deepEqual(
  sortQueueJobs(sortJobs, "updated_desc").map((job) => job.job_id),
  ["a", "b", "c", "d"],
  "updated_desc 保持传入顺序（API 已按更新时间降序）",
);
assert.deepEqual(
  sortQueueJobs(sortJobs, "issues_desc").map((job) => job.job_id),
  ["b", "c", "a", "d"],
  "issues_desc：merged_issue_total 优先、issue_total 兜底、缺失按 0",
);
assert.deepEqual(
  sortQueueJobs(sortJobs, "elapsed_desc").map((job) => job.job_id),
  ["b", "a", "d", "c"],
  "elapsed_desc：耗时降序，未知耗时（null）排在最后而不是冒充 0",
);

// --- paginateJobs -------------------------------------------------------------------

assert.deepEqual(paginateJobs([1, 2, 3, 4, 5], 1, 2), {
  items: [1, 2],
  total: 5,
  page: 1,
  pageCount: 3,
});
assert.deepEqual(paginateJobs([1, 2, 3, 4, 5], 3, 2), { items: [5], total: 5, page: 3, pageCount: 3 });
assert.deepEqual(
  paginateJobs([1, 2, 3, 4, 5], 99, 2),
  { items: [5], total: 5, page: 3, pageCount: 3 },
  "页码超出范围时收敛到最后一页（筛选收紧后不停留在空页）",
);
assert.deepEqual(
  paginateJobs([], 1, 20),
  { items: [], total: 0, page: 1, pageCount: 0 },
  "空结果：pageCount=0，调用方据此不渲染分页器",
);

// --- countAnalyzingJobs：与侧边栏角标同口径 ------------------------------------------

assert.equal(countAnalyzingJobs(null), 0);
assert.equal(
  countAnalyzingJobs(sampleJobs),
  1,
  "processing/queued 归一为 analyzing（与 navBadges.computeNavBadgeCounts 同一归一口径）",
);
assert.equal(countAnalyzingJobs([]), 0, "空列表是真实的 0（不是未知）");

console.log("queuePageAdapters.test.ts: all assertions passed");
