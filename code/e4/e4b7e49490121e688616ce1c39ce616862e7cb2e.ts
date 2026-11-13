import type { JobSummaryRecord } from "@/lib/uiAdapters";
import type { OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";

export type ReportKindFilter = "all" | "budget" | "final" | "unknown";

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

export function groupReportsByFiscalYear(jobs: JobSummaryRecord[]) {
  const groups = new Map<string, JobSummaryRecord[]>();
  for (const job of jobs) {
    const year = typeof job.report_year === "number" && Number.isInteger(job.report_year) && job.report_year > 0
      ? String(job.report_year) : "unknown";
    const reports = groups.get(year) ?? [];
    reports.push(job);
    groups.set(year, reports);
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a === "unknown" ? 1 : b === "unknown" ? -1 : Number(b) - Number(a));
}
