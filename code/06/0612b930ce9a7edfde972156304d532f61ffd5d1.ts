import type { JobSummaryRecord } from "@/lib/uiAdapters";
import type { OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";

export const DIRECTORY_PAGE_SIZE = 6;
export const REPORT_PAGE_SIZE = 10;

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

/** 索引只依赖授权接口返回的组织和任务，避免每张卡片重复扫描全部报告。 */
export function buildReportDirectoryIndex(options: OrganizationFilterOption[], jobs: JobSummaryRecord[]): ReportDirectoryIndex {
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
  for (const job of jobs) {
    const id = String(job.organization_id ?? "");
    const ownStats = stats.get(id);
    if (ownStats) ownStats.ownReportCount += 1;
    for (const ancestor of paths.get(id) ?? []) stats.get(ancestor.id)!.reportCount += 1;
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
