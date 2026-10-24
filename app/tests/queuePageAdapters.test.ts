import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import { canRunQueueJob, requestQueueRun, runQueueBatch, type QueueBatchProgress } from "../app/components/queue/queueBatchActions";
import { ANALYZE_START_REQUEST_BODY } from "../app/components/workspace/uploadCenterAdapters";
import {
  DEFAULT_QUEUE_FILTERS,
  buildQueuePageViewLocally,
  countAnalyzingJobs,
  filterQueueJobs,
  paginateJobs,
  queuePageQuery,
  sortQueueJobs,
} from "../app/components/queue/queuePageAdapters";
import { jobPageSearchParams } from "../lib/jobListViews";
import { resolvePollingDecisionFromActivity } from "../lib/jobPolling";

// --- 服务端分页：查询参数只带非默认值；后端未升级时本地视图与接口同形状 -----------------

assert.equal(
  jobPageSearchParams(queuePageQuery({ ...DEFAULT_QUEUE_FILTERS, keyword: " 教育 ", organizationId: "org-a" }, "updated_desc", 2, 20)).toString(),
  "view=page&page=2&page_size=20&q=%E6%95%99%E8%82%B2&org=org-a&include=runnable",
  "默认筛选不出现在查询串里；队列只匹配组织本身，不带 org_scope",
);
{
  const jobs = [
    { job_id: "run", status: "processing", report_year: 2025 },
    { job_id: "wait", status: "uploaded", report_year: null },
    { job_id: "done-1", status: "done", report_year: 2024 },
    { job_id: "done-2", status: "done", report_year: 2024 },
  ] as JobSummaryRecord[];
  const view = buildQueuePageViewLocally(jobs, DEFAULT_QUEUE_FILTERS, "updated_desc", 9, 2);
  assert.deepEqual(view.items.map((job) => job.job_id), ["done-1", "done-2"], "页码超出范围收敛到最后一页");
  assert.equal(view.page, 2);
  assert.equal(view.page_count, 2);
  assert.deepEqual(view.years, [2025, 2024]);
  assert.deepEqual(view.activity, { running: 1, waiting: 1 });
  assert.deepEqual(view.runnable?.map((job) => job.job_id), ["wait", "done-1", "done-2"], "分析中的任务不可再次运行");
  assert.deepEqual(resolvePollingDecisionFromActivity(view.activity).kind, "active");
  assert.deepEqual(resolvePollingDecisionFromActivity({ running: 0, waiting: 1 }).kind, "waiting");
  assert.deepEqual(resolvePollingDecisionFromActivity({ running: 0, waiting: 0 }).kind, "stop");
  assert.deepEqual(resolvePollingDecisionFromActivity(null).kind, "active", "未拿到汇总按活跃处理，失败后继续重试");
}

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

async function testBatchRuns() {
  for (const status of ["done", "review_required", "error", "cancelled", "uploaded"]) {
    assert.equal(canRunQueueJob({job_id: "run", status}), true);
  }
  for (const status of ["queued", "processing", "running", "unknown", undefined]) {
    assert.equal(canRunQueueJob({job_id: "run", status}), false, `不能重复运行或猜测未知状态：${status}`);
  }
  assert.equal(canRunQueueJob({job_id: "", status: "done"}), false);

  const requests: Array<{url: string; body: unknown}> = [];
  const fetcher: typeof fetch = async (url, init) => {
    requests.push({url: String(url), body: JSON.parse(String(init?.body))});
    return new Response(JSON.stringify({status: "queued"}), {status: 200});
  };
  await requestQueueRun({job_id: "uploaded/1", status: "uploaded"}, fetcher);
  await requestQueueRun({job_id: "done/1", status: "done"}, fetcher);
  assert.deepEqual(requests, [
    {url: "/api/analyze/uploaded%2F1", body: ANALYZE_START_REQUEST_BODY},
    {url: "/api/jobs/done%2F1/reanalyze", body: {mode: "dual", use_local_rules: true}},
  ], "首次分析与重新分析采用各自接口，任务 ID 需要编码");
  await assert.rejects(requestQueueRun({job_id: "busy", status: "processing"}, fetcher), /已跳过/);
  assert.equal(requests.length, 2, "运行中任务不发起请求");
  await assert.rejects(requestQueueRun({job_id: "conflict", status: "done"}, async () => new Response("{}", {status: 409})), /状态已变化/);
  await assert.rejects(requestQueueRun({job_id: "invalid", status: "done"}, async () => new Response(JSON.stringify({detail: {message: "模型配置不可用"}}), {status: 422})), /模型配置不可用/);
  await assert.rejects(requestQueueRun({job_id: "network", status: "done"}, async () => { throw new Error("offline"); }), /网络请求失败/);
  await assert.rejects(requestQueueRun({job_id: "false-success", status: "done"}, async () => new Response(JSON.stringify({success: false, error: "提交失败"}))), /提交失败/);

  const targets: JobSummaryRecord[] = Array.from({length: 8}, (_, index) => ({job_id: `batch-${index}`, status: "done"}));
  targets.push({...targets[0]}, {job_id: "batch-busy", status: "queued"});
  const submitted: string[] = [];
  const progress: QueueBatchProgress[] = [];
  let active = 0;
  let maxActive = 0;
  const result = await runQueueBatch(targets, async (job) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    submitted.push(String(job.job_id));
    await new Promise((resolve) => setTimeout(resolve, 2));
    active -= 1;
    if (job.job_id === "batch-2") throw new Error("权限不足");
  }, (value) => progress.push(value));
  assert.equal(maxActive, 3, "批量提交并发上限为 3");
  assert.equal(submitted.length, 8, "重复 ID 只提交一次，运行中任务跳过");
  assert.equal(new Set(submitted).size, 8);
  assert.equal(result.submittedIds.length, 7);
  assert.deepEqual(result.failures.map((failure) => failure.jobId).sort(), ["batch-2", "batch-busy"]);
  assert.deepEqual(progress[progress.length - 1], {total: 9, completed: 9, submitted: 7, failed: 2});
  const emptyProgress: QueueBatchProgress[] = [];
  assert.deepEqual(await runQueueBatch([], async () => { throw new Error("不得调用"); }, (value) => emptyProgress.push(value)), {submittedIds: [], failures: []});
  assert.deepEqual(emptyProgress, [{total: 0, completed: 0, submitted: 0, failed: 0}]);
  console.log("queuePageAdapters.test.ts: 批量提交、部分失败、状态与接口边界通过");
}
void testBatchRuns().catch((error) => { console.error(error); process.exitCode = 1; });
