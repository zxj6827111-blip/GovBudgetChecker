import { countsFromJobs, type JobPageQuery, type JobPageView } from "../../../lib/jobListViews";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { getDisplayIssueTotal, normalizeUiTaskStatus } from "../../../lib/uiAdapters";

import {
  collectAvailableYears,
  filterWorkbenchQueue,
  type WorkbenchStatusFilter,
} from "../workspace/workbenchAdapters";
import { canRunQueueJob } from "./queueBatchActions";

/**
 * 处理队列全量页（Task 8.1）的纯逻辑层：多维筛选、排序、分页。
 *
 * 设计原则：筛选先复用 workbenchAdapters.filterWorkbenchQueue（关键词/
 * 状态/年份/组织四维与工作台队列面板完全同口径），再叠加本页新增的
 * 文档类型与当前阶段两维——两处队列对同一份数据的筛选语义不会分叉。
 *
 * 阶段筛选基于 stage_progress.phase（Task 3 的规范阶段枚举，
 * 不含 OCR）；phase 缺失/未知的任务用 "unknown" 表示，对应
 * "未知阶段"筛选项，不与任何真实阶段混淆。
 */

export type QueueReportKindFilter = "all" | "budget" | "final" | "unknown";
export type QueueStageFilter =
  | "all"
  | "upload"
  | "pdf_parse"
  | "metadata_recognition"
  | "rule_ai_analysis"
  | "quality_gate"
  | "unknown";

export interface QueuePageFilters {
  keyword: string;
  status: WorkbenchStatusFilter;
  year: string;
  organizationId: string;
  reportKind: QueueReportKindFilter;
  stage: QueueStageFilter;
}

export const DEFAULT_QUEUE_FILTERS: QueuePageFilters = {
  keyword: "",
  status: "all",
  year: "",
  organizationId: "",
  reportKind: "all",
  stage: "all",
};

/** 从复核详情返回时恢复筛选；URL 只承担列表上下文，不参与权限判定。 */
export function queueFiltersFromQuery(params: Pick<URLSearchParams, "get">): QueuePageFilters {
  const status = params.get("status") ?? "all";
  const kind = params.get("kind") ?? "all";
  const stage = params.get("stage") ?? "all";
  return {
    keyword: params.get("q") ?? params.get("job") ?? "", year: params.get("year") ?? "",
    organizationId: params.get("org") ?? "",
    status: ["all", "review_required", "pending_analysis", "analyzing", "failed", "completed"].includes(status) ? status as WorkbenchStatusFilter : "all",
    reportKind: ["all", "budget", "final", "unknown"].includes(kind) ? kind as QueueReportKindFilter : "all",
    stage: Object.prototype.hasOwnProperty.call(QUEUE_STAGE_FILTER_LABELS, stage) ? stage as QueueStageFilter : "all",
  };
}

export function queueReturnPath(filters: QueuePageFilters, sort: QueueSortKey, page: number): string {
  const params = new URLSearchParams();
  if (filters.keyword) params.set("q", filters.keyword);
  if (filters.year) params.set("year", filters.year);
  if (filters.organizationId) params.set("org", filters.organizationId);
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.reportKind !== "all") params.set("kind", filters.reportKind);
  if (filters.stage !== "all") params.set("stage", filters.stage);
  if (sort !== "updated_desc") params.set("sort", sort);
  if (page > 1) params.set("page", String(page));
  return `/queue${params.size ? `?${params}` : ""}`;
}

/** 当前阶段筛选值的展示名（与 PipelineStage 中文标签一致 + 未知）。 */
export const QUEUE_STAGE_FILTER_LABELS: Record<QueueStageFilter, string> = {
  all: "全部阶段",
  upload: "上传",
  pdf_parse: "PDF 解析",
  metadata_recognition: "元数据识别",
  rule_ai_analysis: "规则与 AI 分析",
  quality_gate: "质量门禁",
  unknown: "未知阶段",
};

function jobMatchesReportKind(job: JobSummaryRecord, filter: QueueReportKindFilter): boolean {
  if (filter === "all") {
    return true;
  }
  return String(job.report_kind ?? "unknown") === filter;
}

function resolveJobStageKey(job: JobSummaryRecord): QueueStageFilter {
  const raw = job.stage_progress;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return "unknown";
  }
  const phase = (raw as { phase?: unknown }).phase;
  if (typeof phase === "string" && phase in QUEUE_STAGE_FILTER_LABELS && phase !== "all") {
    return phase as QueueStageFilter;
  }
  return "unknown";
}

function jobMatchesStage(job: JobSummaryRecord, filter: QueueStageFilter): boolean {
  if (filter === "all") {
    return true;
  }
  return resolveJobStageKey(job) === filter;
}

/**
 * 全量页筛选 = 工作台四维筛选（复用 filterWorkbenchQueue）+ 类型 + 阶段。
 * jobs 为 null/undefined 时返回空数组（与 filterWorkbenchQueue 语义一致）。
 */
export function filterQueueJobs(
  jobs: JobSummaryRecord[] | null | undefined,
  filters: QueuePageFilters,
): JobSummaryRecord[] {
  const base = filterWorkbenchQueue(jobs, {
    keyword: filters.keyword,
    status: filters.status,
    year: filters.year,
    organizationId: filters.organizationId,
  });
  return base.filter(
    (job) =>
      jobMatchesReportKind(job, filters.reportKind) && jobMatchesStage(job, filters.stage),
  );
}

// ---------------------------------------------------------------------------
// 排序
// ---------------------------------------------------------------------------

export type QueueSortKey = "updated_desc" | "issues_desc" | "elapsed_desc";

export const QUEUE_SORT_LABELS: Record<QueueSortKey, string> = {
  updated_desc: "按更新时间",
  issues_desc: "按问题数",
  elapsed_desc: "按耗时",
};

function resolveIssueCount(job: JobSummaryRecord): number {
  return getDisplayIssueTotal(job);
}

function resolveElapsedMs(job: JobSummaryRecord): number {
  const value = job.elapsed_ms;
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return value;
  }
  return -1; // 未知耗时排在最后，不冒充 0
}

/** 稳定排序：比较值相同时保持传入顺序（API 已按更新时间降序返回）。 */
export function sortQueueJobs(
  jobs: JobSummaryRecord[],
  sortKey: QueueSortKey,
): JobSummaryRecord[] {
  const result = [...jobs];
  if (sortKey === "issues_desc") {
    result.sort((a, b) => resolveIssueCount(b) - resolveIssueCount(a));
  } else if (sortKey === "elapsed_desc") {
    result.sort((a, b) => resolveElapsedMs(b) - resolveElapsedMs(a));
  }
  // updated_desc：API 返回顺序（ts 降序），不重排
  return result;
}

// ---------------------------------------------------------------------------
// 分页
// ---------------------------------------------------------------------------

export interface QueuePagination<T> {
  items: T[];
  total: number;
  page: number;
  pageCount: number;
}

/**
 * 客户端分页（页码从 1 开始）。
 * 空列表：pageCount = 0、items 为空——"没有结果"不渲染分页器。
 * page 超出范围时自动收敛到最后一页（防止筛选收紧后停留在空页）。
 */
export function paginateJobs<T>(
  jobs: T[],
  page: number,
  pageSize: number,
): QueuePagination<T> {
  const total = jobs.length;
  if (pageSize <= 0 || total === 0) {
    return { items: [], total, page: 1, pageCount: 0 };
  }
  const pageCount = Math.ceil(total / pageSize);
  const clampedPage = Math.min(Math.max(1, page), pageCount);
  const start = (clampedPage - 1) * pageSize;
  return {
    items: jobs.slice(start, start + pageSize),
    total,
    page: clampedPage,
    pageCount,
  };
}

// ---------------------------------------------------------------------------
// 服务端分页（/api/jobs?view=page）
// ---------------------------------------------------------------------------

/** 队列筛选条件 → 接口查询参数。组织只匹配该组织本身，与 filterWorkbenchQueue 一致。 */
export function queuePageQuery(
  filters: QueuePageFilters,
  sortKey: QueueSortKey,
  page: number,
  pageSize: number,
): JobPageQuery {
  return {
    keyword: filters.keyword,
    status: filters.status,
    year: filters.year,
    kind: filters.reportKind,
    stage: filters.stage,
    organizationId: filters.organizationId,
    sort: sortKey,
    page,
    pageSize,
    include: ["runnable"],
  };
}

/**
 * 后端未升级（/api/jobs 仍返回全量数组）时按原口径在本地算出同样形状的分页视图。
 * 年份选项沿用原行为：来自全部任务，不随组织筛选收窄。
 */
export function buildQueuePageViewLocally(
  jobs: JobSummaryRecord[],
  filters: QueuePageFilters,
  sortKey: QueueSortKey,
  page: number,
  pageSize: number,
): JobPageView {
  const filtered = filterQueueJobs(jobs, filters);
  const pagination = paginateJobs(sortQueueJobs(filtered, sortKey), page, pageSize);
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
    runnable: filtered.filter(canRunQueueJob).map((job) => ({
      job_id: String(job.job_id),
      filename: job.filename ?? null,
      status: job.status ?? null,
    })),
  };
}

// ---------------------------------------------------------------------------
// 结果计数（与侧边栏角标同口径）
// ---------------------------------------------------------------------------

/**
 * 当前正在分析的任务数。与 navBadges.computeNavBadgeCounts 的 analyzing
 * 计数完全同源（同一 normalizeUiTaskStatus 归一），供"侧边栏角标 = 本页
 * 筛选结果数"的一致性口径使用——两处数字若不一致会让人以为丢数据。
 */
export function countAnalyzingJobs(jobs: JobSummaryRecord[] | null | undefined): number {
  if (!Array.isArray(jobs)) {
    return 0;
  }
  return jobs.filter((job) => normalizeUiTaskStatus(job.status) === "analyzing").length;
}
