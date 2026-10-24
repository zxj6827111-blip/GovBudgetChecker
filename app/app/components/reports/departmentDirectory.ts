import type { JobSummaryRecord } from "@/lib/uiAdapters";
import type { OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";

export const DIRECTORY_PAGE_SIZE = 24;
export const DIRECTORY_PAGE_SIZES = [12, 24, 48] as const;
export const REPORT_PAGE_SIZE = 10;
/** 单一主体按年度卡片展示时一次取全部年度，切换年度不再请求；超过时提示到汇总列表查看。 */
export const CARD_PAGE_SIZE = 100;
export type ReportManagementScope = "city" | "district";

/** 单位详情仍保留所属部门作为标签切换的根；循环或缺失祖先不猜测归属。 */
export function reportDepartmentId(options: OrganizationFilterOption[], selectedId: string): string {
  const byId = new Map(options.map((option) => [option.id, option]));
  const seen = new Set<string>();
  let current = byId.get(selectedId);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    if (current.level === "department") return current.id;
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return "";
}

// 区名仅作为选择入口；没有真实组织 ID 的区不可打开，不创建虚拟组织和材料。
export const SHANGHAI_DISTRICTS = ["黄浦区", "徐汇区", "长宁区", "静安区", "普陀区", "虹口区", "杨浦区", "闵行区", "宝山区", "嘉定区", "浦东新区", "金山区", "松江区", "青浦区", "奉贤区", "崇明区"] as const;

export function isReportRegion(option: OrganizationFilterOption): boolean {
  return ["province", "city", "district"].includes(option.level);
}

export function reportOrganizationLevelLabel(option: OrganizationFilterOption): string {
  return ({ province: "省级", city: "市级", district: "区级", department: "部门", unit: "单位" } as Record<string, string>)[option.level] ?? "组织";
}

type DirectoryStats = { reportCount: number; ownReportCount: number; unitCount: number };
export type ReportDirectoryIndex = {
  byId: Map<string, OrganizationFilterOption>;
  children: Map<string | null, OrganizationFilterOption[]>;
  paths: Map<string, OrganizationFilterOption[]>;
  stats: Map<string, DirectoryStats>;
  regions: OrganizationFilterOption[];
};

/** 各组织本级报告数（未关联组织的任务不计入）；与 api/job_list_query.organization_report_counts 同口径。 */
export function organizationReportCountsFromJobs(jobs: JobSummaryRecord[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const job of jobs) {
    const id = String(job.organization_id ?? "");
    if (id) counts[id] = (counts[id] ?? 0) + 1;
  }
  return counts;
}

/**
 * 索引只依赖授权接口返回的组织与各组织本级报告数（接口汇总，不需要下载全部报告），
 * 上级合计按可见组织树逐级累加，避免每张卡片重复扫描全部报告。
 */
export function buildReportDirectoryIndex(options: OrganizationFilterOption[], reportCounts: Record<string, number>): ReportDirectoryIndex {
  const byId = new Map(options.map((option) => [option.id, option]));
  const children = new Map<string | null, OrganizationFilterOption[]>();
  const paths = new Map<string, OrganizationFilterOption[]>();
  const stats = new Map<string, DirectoryStats>();
  for (const option of options) {
    // 权限裁剪后上级可能不在可见树中，该节点仍需在根目录可达。
    const parentId = option.parent_id && byId.has(option.parent_id) ? option.parent_id : null;
    const siblings = children.get(parentId) ?? [];
    siblings.push(option);
    children.set(parentId, siblings);
    stats.set(option.id, { reportCount: 0, ownReportCount: 0, unitCount: 0 });
    const path: OrganizationFilterOption[] = [];
    const seen = new Set<string>();
    let current: OrganizationFilterOption | undefined = option;
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      path.unshift(current);
      current = current.parent_id ? byId.get(current.parent_id) : undefined;
    }
    paths.set(option.id, path);
  }
  children.forEach((siblings) => siblings.sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id)));
  for (const option of options) {
    if (option.level !== "unit") continue;
    for (const ancestor of paths.get(option.id) ?? []) {
      if (ancestor.id !== option.id) stats.get(ancestor.id)!.unitCount += 1;
    }
  }
  for (const [id, count] of Object.entries(reportCounts)) {
    const ownStats = stats.get(id);
    if (ownStats) ownStats.ownReportCount += count;
    for (const ancestor of paths.get(id) ?? []) stats.get(ancestor.id)!.reportCount += count;
  }
  const regions = options.filter(isReportRegion).sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id));
  return { byId, children, paths, stats, regions };
}

export function reportDirectoryPath(index: ReportDirectoryIndex, id: string): string {
  return (index.paths.get(id) ?? []).map((option) => option.name).join(" › ");
}

export function reportDirectoryRegion(index: ReportDirectoryIndex, id: string): string {
  const regions = (index.paths.get(id) ?? []).filter(isReportRegion);
  return regions[regions.length - 1]?.id ?? "";
}

export function reportManagementScope(index: ReportDirectoryIndex, id: string): ReportManagementScope {
  return (index.paths.get(id) ?? []).some((item) => item.level === "district") ? "district" : "city";
}

export function reportManagementDirectory(index: ReportDirectoryIndex, scope: ReportManagementScope, regionId: string): ReportDirectoryIndex {
  const options = Array.from(index.byId.values()).filter((item) => {
    if (reportManagementScope(index, item.id) !== scope) return false;
    return !regionId || (index.paths.get(item.id) ?? []).some((ancestor) => ancestor.id === regionId);
  });
  const counts = Object.fromEntries(options.map((item) => [item.id, index.stats.get(item.id)?.ownReportCount ?? 0]));
  return buildReportDirectoryIndex(options, counts);
}

/** 目录首页地址只记录管理范围与所选地区；唯一的市级入口是默认值，不写进地址。 */
export function reportDirectoryHref(index: ReportDirectoryIndex, scope: ReportManagementScope, regionId: string): string {
  const params = new URLSearchParams();
  if (scope === "district") params.set("scope", "district");
  const cities = index.regions.filter((item) => item.level === "city");
  if (regionId && !(scope === "city" && cities.length === 1 && cities[0].id === regionId)) params.set("region", regionId);
  const query = params.toString();
  return query ? `/reports?${query}` : "/reports";
}

/**
 * 部门、单位、地区汇总与待确认归属各有独立地址，后退、刷新和分享都回到同一视图。
 * `tab=units` 仅用于部门的下属单位总览；`self=1` 表示只看部门本级的单位口径材料；
 * `year` 是按年度卡片展示时选中的财政年度。
 */
export function reportDetailHref(organizationId: string, view: { units?: boolean; onlySelf?: boolean; year?: string } = {}): string {
  const params = new URLSearchParams();
  if (view.onlySelf) params.set("self", "1");
  else if (view.units) params.set("tab", "units");
  if (view.year) params.set("year", view.year);
  const query = params.toString();
  return `/reports/${encodeURIComponent(organizationId)}${query ? `?${query}` : ""}`;
}

export type ReportViewState = {
  organizationId: string;
  includeDescendants: boolean;
  reportTab: "department" | "units";
  managementScope: ReportManagementScope;
  regionId: string;
  directoryId: string;
};

/** 选中组织后的目录位置：有下级的组织以自身为目录，叶子回到上级目录。 */
export function reportViewForOrganization(index: ReportDirectoryIndex, id: string, view: { onlySelf?: boolean; units?: boolean } = {}): ReportViewState {
  const option = index.byId.get(id);
  const parentId = option?.parent_id && index.byId.has(option.parent_id) ? option.parent_id : "";
  return {
    organizationId: id,
    includeDescendants: !view.onlySelf,
    reportTab: view.onlySelf || view.units || option?.level === "unit" ? "units" : "department",
    managementScope: reportManagementScope(index, id),
    regionId: reportDirectoryRegion(index, id),
    directoryId: option && (isReportRegion(option) || (index.children.get(id)?.length ?? 0) > 0) ? id : parentId,
  };
}

/**
 * 单一主体（部门汇总、某个单位、部门本级）按年度卡片展示预算与决算；
 * 下属单位总览、地区汇总、待确认归属涉及多个主体，仍用材料列表。
 */
export function reportCardView(index: ReportDirectoryIndex, view: ReportViewState): boolean {
  const selected = index.byId.get(view.organizationId);
  if (!selected || isReportRegion(selected)) return false;
  if (view.reportTab === "department" || !view.includeDescendants) return true;
  return selected.level === "unit" && !(index.children.get(selected.id)?.length);
}

/**
 * 由地址还原页面视图，只在授权组织树内取值，地址不能扩大材料可见范围。
 * 目录首页：唯一的市级入口直接进入；只有区级目录时进入区级范围，唯一的区直接进入。
 */
export function resolveReportView(index: ReportDirectoryIndex, routeOrganizationId: string | undefined, search: URLSearchParams):
  { status: "ready"; view: ReportViewState } | { status: "missing" } {
  const blank: ReportViewState = { organizationId: "", includeDescendants: true, reportTab: "department", managementScope: "city", regionId: "", directoryId: "" };
  if (routeOrganizationId === "unassigned") return { status: "ready", view: { ...blank, organizationId: "unassigned" } };
  if (routeOrganizationId) {
    const option = index.byId.get(routeOrganizationId);
    if (!option) return { status: "missing" };
    const onlySelf = search.get("self") === "1" && !isReportRegion(option) && option.level !== "unit";
    return { status: "ready", view: reportViewForOrganization(index, option.id, { onlySelf, units: search.get("tab") === "units" && !isReportRegion(option) }) };
  }
  const regionView = (id: string): ReportViewState => ({ ...blank, managementScope: reportManagementScope(index, id), organizationId: id, regionId: id, directoryId: id });
  const districts = index.regions.filter((item) => item.level === "district");
  const requestedRegion = index.byId.get(search.get("region") ?? "");
  if (search.get("scope") === "district") {
    // 与切换到“区级部门”标签一致：地址未指定区时停在选区。
    return { status: "ready", view: requestedRegion?.level === "district" ? regionView(requestedRegion.id) : { ...blank, managementScope: "district" } };
  }
  if (requestedRegion && isReportRegion(requestedRegion) && reportManagementScope(index, requestedRegion.id) === "city") return { status: "ready", view: regionView(requestedRegion.id) };
  const cityDepartments = Array.from(index.byId.values()).some((item) => !isReportRegion(item) && reportManagementScope(index, item.id) === "city");
  if (!cityDepartments && districts.length > 0) {
    return { status: "ready", view: districts.length === 1 ? regionView(districts[0].id) : { ...blank, managementScope: "district" } };
  }
  const roots = index.children.get(null) ?? [];
  return { status: "ready", view: roots.length === 1 && isReportRegion(roots[0]) ? regionView(roots[0].id) : blank };
}

/** 按目录逐级累加的计数（与材料数同口径）：own 为组织本级，total 含全部下级。 */
export function aggregateDirectoryCounts(index: ReportDirectoryIndex, counts: Record<string, number>): Map<string, { own: number; total: number }> {
  const totals = new Map<string, { own: number; total: number }>();
  const entry = (id: string) => totals.get(id) ?? totals.set(id, { own: 0, total: 0 }).get(id)!;
  for (const [id, count] of Object.entries(counts)) {
    if (!count || !index.byId.has(id)) continue;
    entry(id).own += count;
    for (const ancestor of index.paths.get(id) ?? []) entry(ancestor.id).total += count;
  }
  return totals;
}

export type DirectorySort = "attention" | "name";

/**
 * “待复核优先”：待复核多的在前，其次材料多的，其余保持名称顺序。
 * 部门本级卡片（pinnedId）始终第一，不参与排序。
 */
export function sortDirectoryItems(items: OrganizationFilterOption[], sort: DirectorySort, rank: (id: string) => { open: number; materials: number }, pinnedId = ""): OrganizationFilterOption[] {
  if (sort === "name") return items;
  const pinned = items.filter((item) => item.id === pinnedId);
  const rest = items.filter((item) => item.id !== pinnedId).map((item, index) => ({ item, index, ...rank(item.id) }));
  rest.sort((a, b) => b.open - a.open || b.materials - a.materials || a.index - b.index);
  return [...pinned, ...rest.map(({ item }) => item)];
}

export function reportDirectoryItems(index: ReportDirectoryIndex, filters: {
  parentId: string; regionId: string; keyword: string; favoritesOnly: boolean; favoriteIds: ReadonlySet<string>;
}): OrganizationFilterOption[] {
  const keyword = filters.keyword.normalize("NFKC").replace(/\s+/g, "").toLowerCase();
  if (keyword || filters.favoritesOnly) {
    return Array.from(index.byId.values()).filter((option) => {
      const inRegion = !filters.regionId || (index.paths.get(option.id) ?? []).some((ancestor) => ancestor.id === filters.regionId);
      const name = option.name.normalize("NFKC").replace(/\s+/g, "").toLowerCase();
      return inRegion && (!keyword || name.includes(keyword)) && (!filters.favoritesOnly || filters.favoriteIds.has(option.id));
    }).sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id));
  }
  const current = index.byId.get(filters.parentId);
  const items = index.children.get(current?.id ?? null) ?? [];
  // 部门本级是一张独立卡片，点击后只查看本级报告；下属单位仍在同一层选择。
  return current && !isReportRegion(current) ? [current, ...items] : items;
}
