import assert from "node:assert/strict";
import { filterDepartmentReports, groupReportsByFiscalYear } from "../app/components/reports/departmentReportAdapters";
import { buildReportDirectoryIndex, reportDirectoryItems, reportDirectoryPath, reportDirectoryRegion } from "../app/components/reports/departmentDirectory";
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
assert.deepEqual(filterDepartmentReports(jobs, organizations, { ...base, includeDescendants: false }).map((job) => job.job_id), ["budget26", "final25"]);

const directoryOrganizations: OrganizationFilterOption[] = [
  { id: "city", name: "上海市", level: "city", parent_id: null, depth: 0 },
  { id: "district", name: "浦东新区", level: "district", parent_id: "city", depth: 1 },
  { id: "dept-a", name: "财政局", level: "department", parent_id: "city", depth: 1 },
  { id: "unit-a", name: "绩效中心", level: "unit", parent_id: "dept-a", depth: 2 },
  { id: "dept-b", name: "财政局", level: "department", parent_id: "district", depth: 2 },
  { id: "unit-b", name: "绩效中心", level: "unit", parent_id: "dept-b", depth: 3 },
  { id: "nested-unit", name: "档案室", level: "unit", parent_id: "unit-a", depth: 3 },
  { id: "orphan", name: "可见单位", level: "unit", parent_id: "hidden-parent", depth: 0 },
];
const directoryJobs: JobSummaryRecord[] = [
  { job_id: "own", organization_id: "dept-a" },
  { job_id: "unit-doc", organization_id: "unit-a" },
  { job_id: "nested-doc", organization_id: "nested-unit" },
  { job_id: "same-name-doc", organization_id: "unit-b" },
  { job_id: "hidden-org", organization_id: "unknown-id" },
  { job_id: "unassigned" },
];
const directory = buildReportDirectoryIndex(directoryOrganizations, directoryJobs);
const directoryFilters = { parentId: "city", regionId: "city", keyword: "", favoritesOnly: false, favoriteIds: new Set<string>() };
assert.deepEqual(new Set(reportDirectoryItems(directory, directoryFilters).map((item) => item.id)), new Set(["dept-a", "district"]));
assert.deepEqual(reportDirectoryItems(directory, { ...directoryFilters, parentId: "dept-a" }).map((item) => item.id), ["dept-a", "unit-a"]);
assert.deepEqual(reportDirectoryItems(directory, { ...directoryFilters, keyword: "绩 效" }).map((item) => item.id), ["unit-a", "unit-b"]);
assert.deepEqual(reportDirectoryItems(directory, { ...directoryFilters, regionId: "district", keyword: "绩效" }).map((item) => item.id), ["unit-b"]);
assert.equal(reportDirectoryPath(directory, "unit-a"), "上海市 › 财政局 › 绩效中心");
assert.equal(reportDirectoryPath(directory, "unit-b"), "上海市 › 浦东新区 › 财政局 › 绩效中心");
assert.equal(reportDirectoryRegion(directory, "unit-a"), "city");
assert.equal(reportDirectoryRegion(directory, "unit-b"), "district");
assert.deepEqual(directory.stats.get("dept-a"), { ownReportCount: 1, reportCount: 3, unitCount: 2 });
assert.deepEqual(directory.stats.get("city"), { ownReportCount: 0, reportCount: 4, unitCount: 3 });
assert.deepEqual(reportDirectoryItems(directory, { ...directoryFilters, parentId: "dept-a", favoritesOnly: true, favoriteIds: new Set(["unit-b", "unknown-id"]) }).map((item) => item.id), ["unit-b"]);
assert.equal(reportDirectoryItems(directory, { ...directoryFilters, keyword: "不存在" }).length, 0);
assert.ok(reportDirectoryItems(directory, { ...directoryFilters, parentId: "", regionId: "" }).some((item) => item.id === "orphan"));
assert.equal(filterDepartmentReports(directoryJobs, directoryOrganizations, { ...base, organizationId: "" }).length, directoryJobs.length, "未知组织 ID 的历史任务仍能在全部报告中查看");
const cyclic = buildReportDirectoryIndex([
  { id: "a", name: "节点甲", level: "department", parent_id: "b", depth: 0 },
  { id: "b", name: "节点乙", level: "unit", parent_id: "a", depth: 1 },
], [{ job_id: "cycle-doc", organization_id: "b" }]);
assert.equal(cyclic.paths.get("a")?.length, 2, "异常循环父子关系不能让页面冻结");
assert.equal(cyclic.stats.get("a")?.reportCount, 1);
console.log("department report identity/year/kind/scope and directory hierarchy/search/count regression tests passed");
