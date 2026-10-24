import { countsFromJobs, type JobPageQuery, type JobPageView } from "../../../lib/jobListViews";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import type { OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";
import { organizationReportCountsFromJobs } from "./departmentDirectory";

export type ReportKindFilter = "all" | "budget" | "final" | "unknown";

export interface DepartmentReportQuery {
  organizationId: string;
  includeDescendants: boolean;
  year: string;
  kind: ReportKindFilter;
  keyword: string;
  reviewStatus?: string;
  managementScope?: "all" | "city" | "district";
  managementRegionId?: string;
  reportCategory?: "all" | "department" | "units";
  /** 从 1 开始。 */
  page: number;
  pageSize: number;
}

/** 报告筛选 → /api/jobs?view=page 参数；口径同 filterDepartmentReports。 */
export function departmentReportPageQuery(query: DepartmentReportQuery): JobPageQuery {
  return {
    keyword: query.keyword,
    year: query.year,
    kind: query.kind,
    organizationId: query.organizationId,
    organizationScope: query.includeDescendants ? "descendants" : "exact",
    page: query.page,
    pageSize: query.pageSize,
    include: ["organization_counts"],
  };
}

/** 后端未升级（/api/jobs 仍返回全量数组）时按原口径在本地算出同样形状的分页视图。 */
export function buildDepartmentReportPageLocally(
  jobs: JobSummaryRecord[], options: OrganizationFilterOption[], query: DepartmentReportQuery,
): JobPageView {
  const scope = filterDepartmentReports(jobs, options, { ...query, year: "", kind: "all", keyword: "" });
  const visible = filterDepartmentReports(jobs, options, query);
  const pageCount = visible.length === 0 ? 0 : Math.ceil(visible.length / query.pageSize);
  const page = pageCount === 0 ? 1 : Math.min(Math.max(1, query.page), pageCount);
  const yearKeys = groupReportsByFiscalYear(scope).map(([year]) => year);
  const counts = countsFromJobs(jobs);
  return {
    items: visible.slice((page - 1) * query.pageSize, page * query.pageSize),
    total: visible.length,
    page,
    page_size: query.pageSize,
    page_count: pageCount,
    years: yearKeys.filter((year) => year !== "unknown").map(Number),
    has_unknown_year: yearKeys.includes("unknown"),
    job_total: counts.total,
    status_counts: counts.status_counts,
    activity: counts.activity,
    organization_report_counts: organizationReportCountsFromJobs(jobs),
  };
}

/** 使用组织 ID 与父子关系归属材料；相同名字不合并，未归属材料单独保留。 */
export function reportOrganizationIds(options: OrganizationFilterOption[], selectedId: string): Set<string> {
  const ids = new Set([selectedId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const option of options) {
      if (option.parent_id && ids.has(option.parent_id) && !ids.has(option.id)) {
        ids.add(option.id);
        changed = true;
      }
    }
  }
  return ids;
}

export function filterDepartmentReports(
  jobs: JobSummaryRecord[], options: OrganizationFilterOption[],
  filters: { organizationId: string; year: string; kind: ReportKindFilter; keyword: string; includeDescendants?: boolean },
): JobSummaryRecord[] {
  const ids = filters.includeDescendants === false ? new Set([filters.organizationId]) : reportOrganizationIds(options, filters.organizationId);
  const keyword = filters.keyword.trim().toLowerCase();
  return jobs.filter((job) => {
    const orgId = String(job.organization_id ?? "");
    if (filters.organizationId === "unassigned" ? Boolean(orgId)
      : filters.organizationId && !ids.has(orgId)) return false;
    const year = typeof job.report_year === "number" && Number.isInteger(job.report_year) && job.report_year > 0
      ? String(job.report_year) : "unknown";
    if (filters.year && filters.year !== year) return false;
    const kind = job.report_kind === "budget" || job.report_kind === "final" ? job.report_kind : "unknown";
    if (filters.kind !== "all" && filters.kind !== kind) return false;
    return !keyword || [job.filename, job.organization_name, job.job_id]
      .some((value) => String(value ?? "").toLowerCase().includes(keyword));
  });
}

export function groupReportsByFiscalYear<T extends { report_year?: number | null }>(jobs: T[]) {
  const groups = new Map<string, T[]>();
  for (const job of jobs) {
    const year = typeof job.report_year === "number" && Number.isInteger(job.report_year) && job.report_year > 0
      ? String(job.report_year) : "unknown";
    const reports = groups.get(year) ?? [];
    reports.push(job);
    groups.set(year, reports);
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a === "unknown" ? 1 : b === "unknown" ? -1 : Number(b) - Number(a));
}
