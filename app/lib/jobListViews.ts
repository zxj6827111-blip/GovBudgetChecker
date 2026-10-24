import type { JobSummaryRecord } from "./uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus } from "./uiAdapters";

/**
 * 任务列表的服务端视图（/api/jobs?view=counts|page|overview）。
 *
 * 此前侧栏、处理队列、部门报告、任务历史、工作台、质量管理都先下载全部任务摘要
 * （277 个任务约 2.3MB）再在浏览器里筛选、分页、计数。现在由接口返回一页数据或
 * 汇总结果（api/job_list_query.py，口径与各页 adapter 一致）。
 *
 * 后端未升级时 /api/jobs 会忽略 view 参数、照旧返回数组：此时调用方传入的
 * 本地构建函数按原口径计算同样形状的结果，页面行为不变。
 */

export interface JobStatusCounts {
  analyzing: number;
  review_required: number;
  pending_analysis: number;
  failed: number;
  completed: number;
}

export interface JobPollingActivity {
  running: number;
  waiting: number;
}

export interface JobCountsView {
  total: number;
  status_counts: JobStatusCounts;
  activity: JobPollingActivity;
}

export interface RunnableJob {
  job_id: string;
  filename?: string | null;
  status?: string | null;
}

export interface JobPageView {
  items: JobSummaryRecord[];
  /** 筛选后的条数。 */
  total: number;
  page: number;
  page_size: number;
  page_count: number;
  years: number[];
  has_unknown_year: boolean;
  /** 全部可见任务数；status_counts / activity 同样覆盖全部可见任务。 */
  job_total: number;
  status_counts: JobStatusCounts;
  activity: JobPollingActivity;
  runnable?: RunnableJob[];
  organization_report_counts?: Record<string, number>;
}

export interface JobCoverageAggregate {
  /** 无样本时为 null。 */
  average: number | null;
  sample_size: number;
  excluded_count: number;
}

export interface JobTrendPoint {
  /** 浏览器所在时区的自然日，YYYY-MM-DD。 */
  date: string;
  uploaded: number;
  analyzed: number;
}

export interface JobOverviewTrend {
  points: JobTrendPoint[];
  missing_upload_times: number;
  missing_analysis_times: number;
}

export interface JobFailureStageCount {
  phase: string;
  label: string;
  count: number;
}

/** 工作台总览与质量管理页的全部任务汇总（不含任务明细）。 */
export interface JobOverviewView extends JobCountsView {
  /** 各组织本级的任务数（不含未关联组织的任务）。 */
  organization_report_counts: Record<string, number>;
  unassociated_count: number;
  /** 工作台口径：只认 0～1 之间的数字。 */
  page_coverage: JobCoverageAggregate;
  /** 质量管理页口径：任何有限数值。 */
  page_coverage_numeric: JobCoverageAggregate;
  coverage_min: number;
  low_coverage_count: number;
  done_coverage: { sample_size: number; low_coverage_count: number } | null;
  failure_stages: JobFailureStageCount[];
  trend: JobOverviewTrend;
}

export interface JobOverviewQuery {
  coverageMin: number;
  trendDays: number;
}

export interface JobPageQuery {
  keyword?: string;
  status?: string;
  year?: string;
  kind?: string;
  stage?: string;
  organizationId?: string;
  /** descendants：含全部下级组织（部门报告）；缺省只匹配该组织本身（处理队列）。 */
  organizationScope?: "exact" | "descendants";
  sort?: string;
  page: number;
  pageSize: number;
  include?: Array<"runnable" | "organization_counts">;
  /** finished：只要已结束的任务（任务历史）。 */
  scope?: "finished";
  /** 只要页面覆盖率低于该值的任务（工作台"页面覆盖率不足"明细）。 */
  coverageBelow?: number;
}

const STATIC_WAITING_RAW_STATUSES = new Set(["uploaded"]);

/** 后端未升级时的本地计数，口径同 api/job_list_query.build_counts_view。 */
export function countsFromJobs(jobs: JobSummaryRecord[]): JobCountsView {
  const statusCounts: JobStatusCounts = { analyzing: 0, review_required: 0, pending_analysis: 0, failed: 0, completed: 0 };
  const activity: JobPollingActivity = { running: 0, waiting: 0 };
  for (const job of jobs) {
    const status = normalizeUiTaskStatus(job.status);
    statusCounts[status as keyof JobStatusCounts] += 1;
    if (isUiTaskFinished(status)) continue;
    const raw = String(job.status ?? "").trim().toLowerCase();
    if ((status as string) === "pending_analysis" || STATIC_WAITING_RAW_STATUSES.has(raw)) activity.waiting += 1;
    else activity.running += 1;
  }
  return { total: jobs.length, status_counts: statusCounts, activity };
}

export function jobPageSearchParams(query: JobPageQuery): URLSearchParams {
  const params = new URLSearchParams({ view: "page", page: String(query.page), page_size: String(query.pageSize) });
  if (query.keyword?.trim()) params.set("q", query.keyword.trim());
  if (query.status && query.status !== "all") params.set("status", query.status);
  if (query.year) params.set("year", query.year);
  if (query.kind && query.kind !== "all") params.set("kind", query.kind);
  if (query.stage && query.stage !== "all") params.set("stage", query.stage);
  if (query.organizationId) params.set("org", query.organizationId);
  if (query.organizationId && query.organizationScope === "descendants") params.set("org_scope", "descendants");
  if (query.sort && query.sort !== "updated_desc") params.set("sort", query.sort);
  if (query.include?.length) params.set("include", query.include.join(","));
  if (query.scope) params.set("scope", query.scope);
  if (query.coverageBelow !== undefined) params.set("coverage_below", String(query.coverageBelow));
  return params;
}

/** 趋势按浏览器所在时区分日，与本地计算（new Date 的本地日期）一致。 */
export function jobOverviewSearchParams(query: JobOverviewQuery, timeZone?: string): URLSearchParams {
  const params = new URLSearchParams({
    view: "overview",
    coverage_min: String(query.coverageMin),
    trend_days: String(query.trendDays),
  });
  if (timeZone) params.set("tz", timeZone);
  return params;
}

function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

async function readJobsResponse(response: Response, label: string): Promise<unknown> {
  if (!response.ok) throw new Error(`${label}读取失败（HTTP ${response.status}），请重试。`);
  return response.json();
}

export async function fetchJobPage(
  query: JobPageQuery,
  buildLocally: (jobs: JobSummaryRecord[]) => JobPageView,
  signal?: AbortSignal,
): Promise<JobPageView> {
  const response = await fetch(`/api/jobs?${jobPageSearchParams(query)}`, { cache: "no-store", signal });
  const payload = await readJobsResponse(response, "任务列表");
  if (Array.isArray(payload)) return buildLocally(payload as JobSummaryRecord[]);
  const view = payload as Partial<JobPageView> | null;
  if (!view || !Array.isArray(view.items) || typeof view.total !== "number" || !view.status_counts || !view.activity) {
    throw new Error("任务列表数据不完整，请重试。");
  }
  return view as JobPageView;
}

export async function fetchJobCounts(signal?: AbortSignal): Promise<JobCountsView> {
  const response = await fetch("/api/jobs?view=counts", { cache: "no-store", signal });
  const payload = await readJobsResponse(response, "任务计数");
  if (Array.isArray(payload)) return countsFromJobs(payload as JobSummaryRecord[]);
  const view = payload as Partial<JobCountsView> | null;
  if (!view || typeof view.total !== "number" || !view.status_counts || !view.activity) {
    throw new Error("任务计数数据不完整，请重试。");
  }
  return view as JobCountsView;
}

export async function fetchJobOverview(
  query: JobOverviewQuery,
  buildLocally: (jobs: JobSummaryRecord[]) => JobOverviewView,
  signal?: AbortSignal,
): Promise<JobOverviewView> {
  const response = await fetch(`/api/jobs?${jobOverviewSearchParams(query, browserTimeZone())}`, { cache: "no-store", signal });
  const payload = await readJobsResponse(response, "任务汇总");
  if (Array.isArray(payload)) return buildLocally(payload as JobSummaryRecord[]);
  const view = payload as Partial<JobOverviewView> | null;
  if (!view || typeof view.total !== "number" || !view.status_counts || !view.activity
      || !view.page_coverage || !view.page_coverage_numeric || !view.trend || !Array.isArray(view.failure_stages)) {
    throw new Error("任务汇总数据不完整，请重试。");
  }
  return view as JobOverviewView;
}
