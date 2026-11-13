import assert from "node:assert/strict";
import { buildDepartmentReportPageLocally, departmentReportPageQuery, filterDepartmentReports, groupReportsByFiscalYear } from "../app/components/reports/departmentReportAdapters";
import { jobPageSearchParams } from "../lib/jobListViews";
import { buildReportDirectoryIndex, organizationReportCountsFromJobs, reportDirectoryItems, reportDirectoryPath, reportDirectoryRegion, reportDepartmentId, reportManagementDirectory, reportManagementScope, DIRECTORY_PAGE_SIZE, DIRECTORY_PAGE_SIZES } from "../app/components/reports/departmentDirectory";
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
const directory = buildReportDirectoryIndex(directoryOrganizations, organizationReportCountsFromJobs(directoryJobs));
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
], organizationReportCountsFromJobs([{ job_id: "cycle-doc", organization_id: "b" }]));
assert.equal(cyclic.paths.get("a")?.length, 2, "异常循环父子关系不能让页面冻结");
assert.equal(cyclic.stats.get("a")?.reportCount, 1);
// 服务端分页：部门报告按"部门及下属单位"取数；后端未升级时本地视图与接口同口径。
const pageQuery = { organizationId: "dept", includeDescendants: true, year: "", kind: "all" as const, keyword: "", page: 1, pageSize: 2 };
assert.equal(jobPageSearchParams(departmentReportPageQuery(pageQuery)).get("org_scope"), "descendants");
assert.equal(jobPageSearchParams(departmentReportPageQuery({ ...pageQuery, includeDescendants: false })).get("org_scope"), null);
const localPage = buildDepartmentReportPageLocally(jobs, organizations, { ...pageQuery, page: 5 });
assert.deepEqual(localPage.items.map((job) => job.job_id), ["final24"], "超出范围的页码收敛到最后一页");
assert.deepEqual([localPage.total, localPage.page, localPage.page_count], [3, 2, 2]);
assert.deepEqual(localPage.years, [2026, 2025, 2024]);
assert.equal(localPage.has_unknown_year, false, "年份选项只看组织范围内的报告");
assert.equal(buildDepartmentReportPageLocally(jobs, organizations, { ...pageQuery, organizationId: "" }).has_unknown_year, true);
assert.deepEqual(localPage.organization_report_counts, { dept: 2, unit: 1, other: 1 });
console.log("department report identity/year/kind/scope and directory hierarchy/search/count regression tests passed");

assert.equal(DIRECTORY_PAGE_SIZE, 24);
assert.deepEqual(DIRECTORY_PAGE_SIZES, [12, 24, 48]);
const cityDirectory = reportManagementDirectory(directory, "city", "city");
const districtDirectory = reportManagementDirectory(directory, "district", "district");
assert.equal(reportManagementScope(directory, "nested-unit"), "city");
assert.equal(reportManagementScope(directory, "unit-b"), "district");
assert.deepEqual(reportDirectoryItems(cityDirectory, directoryFilters).map((item) => item.id), ["dept-a"]);
assert.deepEqual(reportDirectoryItems(cityDirectory, { ...directoryFilters, keyword: "绩效" }).map((item) => item.id), ["unit-a"], "市级搜索不得混入区级同名单位");
assert.equal(cityDirectory.stats.get("city")?.reportCount, 3, "市级汇总排除区级材料");
assert.equal(cityDirectory.stats.get("city")?.unitCount, 2);
assert.deepEqual(reportDirectoryItems(districtDirectory, { ...directoryFilters, parentId: "district", regionId: "district" }).map((item) => item.id), ["dept-b"]);
assert.deepEqual(reportDirectoryItems(cityDirectory, { ...directoryFilters, favoritesOnly: true, favoriteIds: new Set(["unit-b", "unit-a"]) }).map((item) => item.id), ["unit-a"], "收藏同样遵守管理范围");

assert.equal(reportDepartmentId(directoryOrganizations, "nested-unit"), "dept-a");
assert.equal(reportDepartmentId(directoryOrganizations, "dept-b"), "dept-b");
assert.equal(reportDepartmentId(directoryOrganizations, "orphan"), "");
assert.equal(reportDepartmentId(directoryOrganizations, "missing"), "");
assert.equal(reportDepartmentId([
  { id: "a", name: "甲", level: "unit", parent_id: "b", depth: 0 },
  { id: "b", name: "乙", level: "unit", parent_id: "a", depth: 0 },
], "a"), "", "循环祖先不得造成无限遍历或猜测部门归属");
