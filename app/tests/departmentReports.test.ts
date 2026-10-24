import assert from "node:assert/strict";
import { buildDepartmentReportPageLocally, departmentReportPageQuery, filterDepartmentReports, groupReportsByFiscalYear } from "../app/components/reports/departmentReportAdapters";
import { jobPageSearchParams } from "../lib/jobListViews";
import { aggregateDirectoryCounts, buildReportDirectoryIndex, reportCardView, organizationReportCountsFromJobs, sortDirectoryItems, reportDetailHref, reportDirectoryHref, reportDirectoryItems, reportDirectoryPath, reportDirectoryRegion, reportDepartmentId, reportManagementDirectory, reportManagementScope, reportViewForOrganization, resolveReportView, DIRECTORY_PAGE_SIZE, DIRECTORY_PAGE_SIZES } from "../app/components/reports/departmentDirectory";
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

// 部门报告地址：目录首页只记管理范围与地区，详情按组织独立地址，地址只能在授权组织树内还原。
const search = (value = "") => new URLSearchParams(value);
assert.equal(reportDirectoryHref(directory, "city", "city"), "/reports", "唯一的市级入口是默认值，不写进地址");
assert.equal(reportDirectoryHref(directory, "district", ""), "/reports?scope=district");
assert.equal(reportDirectoryHref(directory, "district", "district"), "/reports?scope=district&region=district");
assert.equal(reportDetailHref("dept-a"), "/reports/dept-a");
assert.equal(reportDetailHref("dept-a", { units: true }), "/reports/dept-a?tab=units");
assert.equal(reportDetailHref("dept-a", { units: true, onlySelf: true }), "/reports/dept-a?self=1");
assert.equal(reportDetailHref("单位 1/2"), "/reports/%E5%8D%95%E4%BD%8D%201%2F2");
assert.deepEqual(resolveReportView(directory, undefined, search()), { status: "ready", view: { organizationId: "", includeDescendants: true, reportTab: "department", managementScope: "city", regionId: "", directoryId: "" } }, "多个根节点时首页不自动选范围");
const cityOnly = buildReportDirectoryIndex(directoryOrganizations.filter((item) => item.id !== "orphan"), {});
assert.deepEqual(resolveReportView(cityOnly, undefined, search()), { status: "ready", view: { organizationId: "city", includeDescendants: true, reportTab: "department", managementScope: "city", regionId: "city", directoryId: "city" } }, "唯一的市级入口直接进入");
assert.deepEqual(resolveReportView(cityOnly, undefined, search("scope=district")), { status: "ready", view: { organizationId: "", includeDescendants: true, reportTab: "department", managementScope: "district", regionId: "", directoryId: "" } }, "未指定区时停在选区");
assert.deepEqual(resolveReportView(cityOnly, undefined, search("scope=district&region=district")), { status: "ready", view: { organizationId: "district", includeDescendants: true, reportTab: "department", managementScope: "district", regionId: "district", directoryId: "district" } });
assert.equal((resolveReportView(cityOnly, undefined, search("scope=district&region=dept-a")) as { view: { organizationId: string } }).view.organizationId, "", "地区参数只接受区级组织");
assert.equal((resolveReportView(cityOnly, undefined, search("organization=dept-a")) as { view: { organizationId: string } }).view.organizationId, "city", "旧版 organization 参数由页面跳转处理，不在首页选中部门");
const districtOnly = buildReportDirectoryIndex(directoryOrganizations.filter((item) => ["district", "dept-b", "unit-b"].includes(item.id)), {});
assert.equal((resolveReportView(districtOnly, undefined, search()) as { view: { managementScope: string; regionId: string } }).view.regionId, "district", "只有一个区时直接进入该区");
assert.deepEqual(resolveReportView(cityOnly, "dept-a", search()), { status: "ready", view: { organizationId: "dept-a", includeDescendants: true, reportTab: "department", managementScope: "city", regionId: "city", directoryId: "dept-a" } });
assert.deepEqual(resolveReportView(cityOnly, "dept-a", search("tab=units")), { status: "ready", view: { organizationId: "dept-a", includeDescendants: true, reportTab: "units", managementScope: "city", regionId: "city", directoryId: "dept-a" } });
assert.deepEqual(resolveReportView(cityOnly, "dept-a", search("self=1")), { status: "ready", view: { organizationId: "dept-a", includeDescendants: false, reportTab: "units", managementScope: "city", regionId: "city", directoryId: "dept-a" } });
assert.deepEqual(resolveReportView(cityOnly, "unit-b", search("self=1")), { status: "ready", view: { organizationId: "unit-b", includeDescendants: true, reportTab: "units", managementScope: "district", regionId: "district", directoryId: "dept-b" } }, "单位不接受本级参数，按区级范围还原");
assert.deepEqual(resolveReportView(cityOnly, "city", search("tab=units")), { status: "ready", view: { organizationId: "city", includeDescendants: true, reportTab: "department", managementScope: "city", regionId: "city", directoryId: "city" } }, "地区汇总不分部门/单位口径");
assert.deepEqual(resolveReportView(cityOnly, "missing", search()), { status: "missing" }, "授权树外的组织不能经地址打开");
assert.equal((resolveReportView(cityOnly, "unassigned", search()) as { view: { organizationId: string } }).view.organizationId, "unassigned");
assert.deepEqual(reportViewForOrganization(cityOnly, "unit-a"), { organizationId: "unit-a", includeDescendants: true, reportTab: "units", managementScope: "city", regionId: "city", directoryId: "unit-a" }, "有下级的单位以自身为目录");
console.log("department report route view regression tests passed");

// 待复核数按目录逐级累加；“待复核优先”排序保留部门本级卡片在第一位。
const openTotals = aggregateDirectoryCounts(cityOnly, { "dept-a": 2, "unit-a": 1, "nested-unit": 4, "unit-b": 5, "missing": 7, "district": 0 });
assert.deepEqual(openTotals.get("dept-a"), { own: 2, total: 7 });
assert.deepEqual(openTotals.get("city"), { own: 0, total: 12 });
assert.deepEqual(openTotals.get("district"), { own: 0, total: 5 });
assert.equal(openTotals.has("missing"), false, "目录外组织不计入");
const sortable = ["dept-a", "unit-a", "nested-unit", "unit-b"].map((id) => cityOnly.byId.get(id)!);
const rank = (id: string) => ({ open: openTotals.get(id)?.own ?? 0, materials: id === "unit-a" ? 3 : 0 });
assert.deepEqual(sortDirectoryItems(sortable, "name", rank).map((item) => item.id), ["dept-a", "unit-a", "nested-unit", "unit-b"]);
assert.deepEqual(sortDirectoryItems(sortable, "attention", rank).map((item) => item.id), ["unit-b", "nested-unit", "dept-a", "unit-a"]);
assert.deepEqual(sortDirectoryItems(sortable, "attention", rank, "dept-a").map((item) => item.id), ["dept-a", "unit-b", "nested-unit", "unit-a"]);
assert.deepEqual(sortDirectoryItems(sortable, "attention", () => ({ open: 0, materials: 0 })).map((item) => item.id), ["dept-a", "unit-a", "nested-unit", "unit-b"], "无待复核、无材料时保持名称顺序");
console.log("department directory open-review totals and sorting passed");

// 单一主体（部门汇总、单位、部门本级）按年度卡片；下属单位总览、地区汇总、待确认归属仍用列表。
const viewOf = (id: string, search = "") => (resolveReportView(cityOnly, id, new URLSearchParams(search)) as { view: Parameters<typeof reportCardView>[1] }).view;
assert.equal(reportCardView(cityOnly, viewOf("dept-a")), true);
assert.equal(reportCardView(cityOnly, viewOf("dept-a", "tab=units")), false);
assert.equal(reportCardView(cityOnly, viewOf("dept-a", "self=1")), true);
assert.equal(reportCardView(cityOnly, viewOf("unit-b")), true);
assert.equal(reportCardView(cityOnly, viewOf("unit-a")), false, "有下级单位的单位涉及多个主体");
assert.equal(reportCardView(cityOnly, viewOf("city")), false);
assert.equal(reportCardView(cityOnly, viewOf("unassigned")), false);
assert.equal(reportDetailHref("dept-a", { year: "2025" }), "/reports/dept-a?year=2025");
assert.equal(reportDetailHref("dept-a", { onlySelf: true, year: "unknown" }), "/reports/dept-a?self=1&year=unknown");
console.log("department report card view regression tests passed");
