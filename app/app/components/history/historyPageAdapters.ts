import { countsFromJobs, type JobPageQuery, type JobPageView, type JobStatusCounts } from "../../../lib/jobListViews";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus } from "../../../lib/uiAdapters";

import {
  filterQueueJobs,
  paginateJobs,
  queuePageQuery,
  sortQueueJobs,
  type QueuePageFilters,
  type QueueSortKey,
} from "../queue/queuePageAdapters";
import { collectAvailableYears } from "../workspace/workbenchAdapters";

/**
 * 任务历史页（Task 8.2）的纯逻辑层：终态任务筛选 + 报告下载链接。
 *
 * 终态口径：status 归一后为 completed / review_required / failed
 * （复用 isUiTaskFinished，与队列表"是否跑完"的既有判定同源），
 * queued / processing 属于"还在跑"，不在历史检索范围。
 */

/**
 * 先过滤终态任务，再应用与处理队列页完全一致的多维筛选
 * （filterQueueJobs 内部复用工作台四维 + 类型 + 阶段）。
 * jobs 为 null/undefined 时返回空数组。
 */
export function filterHistoryJobs(
  jobs: JobSummaryRecord[] | null | undefined,
  filters: QueuePageFilters,
): JobSummaryRecord[] {
  if (!Array.isArray(jobs)) {
    return [];
  }
  const terminalJobs = jobs.filter((job) => isUiTaskFinished(normalizeUiTaskStatus(job.status)));
  return filterQueueJobs(terminalJobs, filters);
}

export type ReportDownloadFormat = "pdf" | "csv" | "json";

export const REPORT_DOWNLOAD_FORMATS: ReportDownloadFormat[] = ["pdf", "csv", "json"];

/**
 * 报告下载链接：接既有 /api/reports/download（require_job_access 鉴权，
 * 前端只负责构造 URL，不复制任何鉴权逻辑）。
 */
export function buildReportDownloadUrl(jobId: string, format: ReportDownloadFormat): string {
  return `/api/reports/download?job_id=${encodeURIComponent(jobId)}&format=${format}`;
}

/** 终态任务计数（页面结果说明用）。 */
export function countTerminalJobs(jobs: JobSummaryRecord[] | null | undefined): number {
  if (!Array.isArray(jobs)) {
    return 0;
  }
  return jobs.filter((job) => isUiTaskFinished(normalizeUiTaskStatus(job.status))).length;
}

/** 终态任务数，取自接口对全部可见任务的状态计数。 */
export function terminalCountFromStatusCounts(counts: JobStatusCounts | null | undefined): number | null {
  return counts ? counts.completed + counts.review_required + counts.failed : null;
}

/** 历史筛选 → /api/jobs?view=page 参数：与处理队列同一套筛选，外加只看已结束的任务。 */
export function historyPageQuery(
  filters: QueuePageFilters,
  sortKey: QueueSortKey,
  page: number,
  pageSize: number,
): JobPageQuery {
  return { ...queuePageQuery(filters, sortKey, page, pageSize), include: undefined, scope: "finished" };
}

/** 后端未升级（/api/jobs 仍返回全量数组）时按原口径在本地算出同样形状的分页视图。 */
export function buildHistoryPageViewLocally(
  jobs: JobSummaryRecord[],
  filters: QueuePageFilters,
  sortKey: QueueSortKey,
  page: number,
  pageSize: number,
): JobPageView {
  const pagination = paginateJobs(sortQueueJobs(filterHistoryJobs(jobs, filters), sortKey), page, pageSize);
  const counts = countsFromJobs(jobs);
  return {
    items: pagination.items,
    total: pagination.total,
    page: pagination.page,
    page_size: pageSize,
    page_count: pagination.pageCount,
    years: collectAvailableYears(jobs),
    has_unknown_year: jobs.some((job) => job.report_year === null || job.report_year === undefined),
    job_total: counts.total,
    status_counts: counts.status_counts,
    activity: counts.activity,
  };
}
