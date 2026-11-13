import assert from "node:assert/strict";
import { filterDepartmentReports, groupReportsByFiscalYear } from "../app/components/reports/departmentReportAdapters";
import type { OrganizationFilterOption } from "../app/components/workspace/OrganizationFilterSelect";
import type { JobSummaryRecord } from "../lib/uiAdapters";

const organizations: OrganizationFilterOption[] = [
  { id: "dept", name: "部门甲", level: "department", parent_id: null, depth: 0 },
  { id: "unit", name: "单位甲", level: "unit", parent_id: "dept", depth: 1 },
  { id: "other", name: "部门甲", level: "department", parent_id: null, depth: 0 },
];
const jobs: JobSummaryRecord[] = [
  { job_id: "budget26", organization_id: "dept", report_year: 2026, report_kind: "budget", filename: "预算.pdf" },
  { job_id: "final25", organization_id: "dept", report_year: 2025, report_kind: "final" },
  { job_id: "final24", organization_id: "unit", report_year: 2024, report_kind: "final", created_ts: 1790000000 },
  { job_id: "same-name", organization_id: "other", report_year: 2025, report_kind: "final" },
  { job_id: "unassigned", report_year: null, report_kind: "unknown" },
];
const base = { organizationId: "dept", year: "", kind: "all" as const, keyword: "" };
const filtered = filterDepartmentReports(jobs, organizations, base);
assert.deepEqual(filtered.map((job) => job.job_id), ["budget26", "final25", "final24"]);
assert.deepEqual(groupReportsByFiscalYear(filtered).map(([year]) => year), ["2026", "2025", "2024"]);
assert.deepEqual(filterDepartmentReports(jobs, organizations, { ...base, year: "2024", kind: "final" }).map((job) => job.job_id), ["final24"]);
assert.deepEqual(filterDepartmentReports(jobs, organizations, { ...base, kind: "budget" }).map((job) => job.job_id), ["budget26"]);
assert.deepEqual(filterDepartmentReports(jobs, organizations, { ...base, organizationId: "unassigned" }).map((job) => job.job_id), ["unassigned"]);
assert.deepEqual(groupReportsByFiscalYear(jobs).map(([year]) => year), ["2026", "2025", "2024", "unknown"]);
console.log("department report identity/year/kind regression tests passed");
