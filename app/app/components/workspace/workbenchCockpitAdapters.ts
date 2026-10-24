import { countsFromJobs, type JobCountsView, type JobOverviewTrend, type JobPageQuery, type JobPageView } from "../../../lib/jobListViews";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { normalizeUiTaskStatus } from "../../../lib/uiAdapters";
import type { MaterialCoverageSummary } from "../materials/materialLedgerAdapters";
import { paginateJobs } from "../queue/queuePageAdapters";
import type { OrganizationFilterOption } from "./OrganizationFilterSelect";
import { collectAvailableYears, filterWorkbenchQueue, type WorkbenchStatusFilter } from "./workbenchAdapters";

export type CockpitState = "completed" | "review" | "processing" | "failed" | "other";
export interface CockpitProgress {
  source: "ledger" | "jobs";
  total: number;
  completed: number | null;
  review: number;
  processing: number;
  failed: number;
  other: number;
  completionRatio: number | null;
}

const MATERIAL_STATES = ["not_due", "missing", "uploaded", "processing", "review_required",
  "reviewing", "completed", "not_applicable", "mapping_required", "failed"] as const;

/** 拒绝不完整的统计响应：缺字段不补零，也不把分析完成当成人工复核完成。 */
export function validCockpitLedger(value: MaterialCoverageSummary | null): boolean {
  if (!value || !Number.isInteger(value.slot_total) || value.slot_total < 0 || !value.status_counts) return false;
  return MATERIAL_STATES.every((key) => Number.isInteger(value.status_counts[key]) && value.status_counts[key] >= 0)
    && MATERIAL_STATES.reduce((total, key) => total + value.status_counts[key], 0) === value.slot_total;
}

/** 任务侧只需要全部可见任务的总数与状态计数（/api/jobs?view=overview 的汇总）。 */
export function buildCockpitProgress(
  jobs: Pick<JobCountsView, "total" | "status_counts"> | null,
  ledger: MaterialCoverageSummary | null,
): CockpitProgress | null {
  // 台账为空但存在旧任务时，旧任务不能从总览消失；同时不能给它们编造复核结论。
  if (validCockpitLedger(ledger) && ledger && (ledger.slot_total > 0 || jobs?.total === 0)) {
    const counts = ledger.status_counts;
    const review = counts.review_required + counts.reviewing;
    return {
      source: "ledger", total: ledger.slot_total, completed: counts.completed,
      review, processing: counts.processing, failed: counts.failed,
      other: ledger.slot_total - counts.completed - review - counts.processing - counts.failed,
      completionRatio: ledger.slot_total > 0 ? counts.completed / ledger.slot_total : null,
    };
  }
  if (jobs === null) return null;
  const { review_required: review, analyzing: processing, failed } = jobs.status_counts;
  return { source: "jobs", total: jobs.total, completed: null, review, processing, failed,
    other: jobs.total - review - processing - failed, completionRatio: null };
}

export interface DepartmentCoverage {
  total: number;
  covered: number;
  ratio: number | null;
  uncovered: OrganizationFilterOption[];
  departments: Array<OrganizationFilterOption & { covered: boolean }>;
  unlinkedJobs: number;
}

/**
 * 下属单位的材料归入真实父部门；同名、重复任务和组织环都不能制造覆盖。
 * 输入是各组织本级的任务数与任务总数，未关联组织的任务 = 总数 − 已关联到组织的任务。
 */
export function buildDepartmentCoverage(
  jobs: { total: number; organization_report_counts: Record<string, number> } | null,
  organizations: OrganizationFilterOption[] | null,
): DepartmentCoverage | null {
  if (jobs === null || organizations === null) return null;
  const index = new Map(organizations.map((org) => [org.id, org]));
  const departments = Array.from(index.values()).filter((org) => org.level === "department");
  const covered = new Set<string>();
  let linkedJobs = 0;
  for (const [organizationId, count] of Object.entries(jobs.organization_report_counts)) {
    let id = organizationId;
    const seen = new Set<string>();
    while (id && !seen.has(id)) {
      seen.add(id);
      const org = index.get(id);
      if (!org) break;
      if (org.level === "department") { covered.add(org.id); linkedJobs += count; break; }
      id = org.parent_id ?? "";
    }
  }
  const unlinkedJobs = jobs.total - linkedJobs;
  return { total: departments.length, covered: covered.size,
    ratio: departments.length ? covered.size / departments.length : null,
    uncovered: departments.filter((org) => !covered.has(org.id)),
    departments: departments.map((org) => ({ ...org, covered: covered.has(org.id) })), unlinkedJobs };
}

export function cockpitPageCoverage(jobs: JobSummaryRecord[] | null) {
  if (jobs === null) return null;
  const values = jobs.map((job) => job.page_coverage).filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1,
  );
  return { averageRatio: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
    sampleSize: values.length, excludedCount: jobs.length - values.length };
}

export function lowCoverageJobs(jobs: JobSummaryRecord[] | null, minimum: number): JobSummaryRecord[] {
  return (jobs ?? []).filter((job) => typeof job.page_coverage === "number"
    && Number.isFinite(job.page_coverage) && job.page_coverage >= 0 && job.page_coverage < minimum);
}

export interface CockpitTrendPoint { date: string; label: string; uploaded: number; analyzed: number }
export interface CockpitTrend {
  points: CockpitTrendPoint[];
  uploadedTotal: number;
  analyzedTotal: number;
  missingUploadTimes: number;
  missingAnalysisTimes: number;
}

export function cockpitTimestamp(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return value > 1_000_000_000_000 ? value : value * 1000;
}

function localDateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** 用事件发生时间画趋势。updated_ts 可能由元数据编辑改变，绝不拿它冒充完成时间。 */
export function buildCockpitTrend(jobs: JobSummaryRecord[] | null, days: 7 | 30, nowMs = Date.now()): CockpitTrend | null {
  if (jobs === null) return null;
  const today = new Date(nowMs);
  const points: CockpitTrendPoint[] = Array.from({ length: days }, (_, index) => {
    const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() - days + 1 + index);
    return { date: localDateKey(date), label: `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`,
      uploaded: 0, analyzed: 0 };
  });
  const byDate = new Map(points.map((point) => [point.date, point]));
  let missingUploadTimes = 0;
  let missingAnalysisTimes = 0;
  for (const job of jobs) {
    const created = cockpitTimestamp(job.created_ts);
    if (created === null) missingUploadTimes += 1;
    else if (created <= nowMs) { const point = byDate.get(localDateKey(new Date(created))); if (point) point.uploaded += 1; }
    const status = normalizeUiTaskStatus(job.status);
    if (status !== "completed" && status !== "review_required") continue;
    const finished = cockpitTimestamp(job.analysis_finished_ts);
    if (finished === null) missingAnalysisTimes += 1;
    else if (finished <= nowMs) { const point = byDate.get(localDateKey(new Date(finished))); if (point) point.analyzed += 1; }
  }
  return { points, uploadedTotal: points.reduce((sum, point) => sum + point.uploaded, 0),
    analyzedTotal: points.reduce((sum, point) => sum + point.analyzed, 0), missingUploadTimes, missingAnalysisTimes };
}

/** 接口按天返回最近若干天（浏览器时区），这里取最后 days 天并补上展示标签。 */
export function cockpitTrendFromDaily(trend: JobOverviewTrend | null, days: 7 | 30): CockpitTrend | null {
  if (trend === null) return null;
  const points = trend.points.slice(-days).map((point) => ({
    ...point,
    label: `${point.date.slice(5, 7)}.${point.date.slice(8, 10)}`,
  }));
  return { points, uploadedTotal: points.reduce((sum, point) => sum + point.uploaded, 0),
    analyzedTotal: points.reduce((sum, point) => sum + point.analyzed, 0),
    missingUploadTimes: trend.missing_upload_times, missingAnalysisTimes: trend.missing_analysis_times };
}

export function cockpitPercent(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 1000) / 10}%` : "—";
}

export function cockpitOverviewCsv(progress: CockpitProgress | null, coverage: DepartmentCoverage | null,
  pages: ReturnType<typeof cockpitPageCoverage>, trend: CockpitTrend | null, days: number): string {
  const source = progress?.source === "ledger" ? "已建档材料台账" : "可见分析任务（复核结论未知）";
  const rows: Array<Array<string | number>> = [
    ["数据项", "数值", "单位", "统计口径"],
    ["总量", progress?.total ?? "—", "条", source],
    ["待人工复核", progress?.review ?? "—", "条", source],
    ["处理失败", progress?.failed ?? "—", "条", source],
    ["已完成人工复核", progress?.completed ?? "—", "条", "只取材料台账已完成状态"],
    ["已复核占比", cockpitPercent(progress?.completionRatio), "", source],
    ["有已关联材料的部门", coverage?.covered ?? "—", "个", "可见组织树与任务组织关联"],
    ["可见部门总数", coverage?.total ?? "—", "个", "可见组织树中的部门层级"],
    ["未关联到部门的任务", coverage?.unlinkedJobs ?? "—", "个", "不根据单位名称猜测"],
    ["页面平均覆盖率", cockpitPercent(pages?.averageRatio), "", `有效样本${pages?.sampleSize ?? 0}个；缺失或无效样本${pages?.excludedCount ?? 0}个`],
    [`近${days}日新增任务`, trend?.uploadedTotal ?? "—", "个", "真实创建时间"],
    [`近${days}日结束分析`, trend?.analyzedTotal ?? "—", "个", "真实分析结束时间；不代表人工复核完成"],
  ];
  return "\uFEFF" + rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\r\n");
}

// ---------------------------------------------------------------------------
// 抽屉里的任务明细（/api/jobs?view=page）
// ---------------------------------------------------------------------------

export const COCKPIT_TASK_PAGE_SIZE = 20;

export interface CockpitTaskKey {
  /** 每次打开抽屉加一：重新打开时总是重新取数，不显示上一次打开时的旧列表。 */
  session: number;
  lowCoverage: boolean;
  keyword: string;
  status: WorkbenchStatusFilter;
  year: string;
  organizationId: string;
  page: number;
}

export function sameCockpitTaskKey(a: CockpitTaskKey, b: CockpitTaskKey): boolean {
  return a.session === b.session && a.lowCoverage === b.lowCoverage && a.keyword === b.keyword
    && a.status === b.status && a.year === b.year && a.organizationId === b.organizationId && a.page === b.page;
}

/** 筛选口径同 filterWorkbenchQueue（组织只匹配本级）；覆盖率不足同 lowCoverageJobs。 */
export function cockpitTaskQuery(key: CockpitTaskKey, coverageMin: number): JobPageQuery {
  return {
    keyword: key.keyword,
    status: key.status,
    year: key.year,
    organizationId: key.organizationId,
    page: key.page,
    pageSize: COCKPIT_TASK_PAGE_SIZE,
    coverageBelow: key.lowCoverage ? coverageMin : undefined,
  };
}

/** 后端未升级（/api/jobs 仍返回全量数组）时按原口径在本地算出同样形状的分页视图。 */
export function buildCockpitTaskPageLocally(jobs: JobSummaryRecord[], key: CockpitTaskKey, coverageMin: number): JobPageView {
  const base = key.lowCoverage ? lowCoverageJobs(jobs, coverageMin) : jobs;
  const filtered = filterWorkbenchQueue(base, key);
  const pagination = paginateJobs(filtered, key.page, COCKPIT_TASK_PAGE_SIZE);
  const counts = countsFromJobs(jobs);
  return {
    items: pagination.items,
    total: pagination.total,
    page: pagination.page,
    page_size: COCKPIT_TASK_PAGE_SIZE,
    page_count: pagination.pageCount,
    years: collectAvailableYears(jobs),
    has_unknown_year: jobs.some((job) => job.report_year === null || job.report_year === undefined),
    job_total: counts.total,
    status_counts: counts.status_counts,
    activity: counts.activity,
  };
}
