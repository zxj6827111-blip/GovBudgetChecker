"use client";

import Link from "next/link";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Building2, ChevronLeft, ChevronRight, FolderOpen, LayoutGrid, List, RefreshCw, Search, Star } from "lucide-react";
import { Badge, Metric } from "@/components/ui";
import { resolvePollingDecisionFromActivity } from "@/lib/jobPolling";
import { directoryMaterialStatus, openReviewCount, type LatestMaterialStatus, type MaterialReportPage, type OrganizationReviewStatus } from "./materialReportAdapters";
import { MaterialReportCard, MaterialReportCardEmpty } from "./MaterialReportCard";
import { MaterialReportTable } from "./MaterialReportTable";
import { flattenOrganizationTree, type OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";
import { useJobPolling } from "../workspace/useJobPolling";
import {
  groupReportsByFiscalYear,
  type DepartmentReportQuery, type ReportKindFilter,
} from "./departmentReportAdapters";
import { aggregateDirectoryCounts, buildReportDirectoryIndex, CARD_PAGE_SIZE, DIRECTORY_PAGE_SIZE, reportCardView, sortDirectoryItems, type DirectorySort, DIRECTORY_PAGE_SIZES, SHANGHAI_DISTRICTS, isReportRegion, REPORT_PAGE_SIZE, reportDetailHref, reportDirectoryHref, reportDirectoryItems, reportDirectoryPath, reportDepartmentId, reportOrganizationLevelLabel, reportManagementDirectory, reportManagementScope, reportViewForOrganization, resolveReportView, type ReportManagementScope, type ReportViewState } from "./departmentDirectory";

const KINDS: Array<[ReportKindFilter, string]> = [["all", "全部材料"], ["budget", "预算"], ["final", "决算"], ["unknown", "类型待确认"]];
const REPORT_TABS = [["department", "部门"], ["units", "下属单位"]] as const;
const DIRECTORY_VIEW_KEY = "gbc:reports:directory-view";
const DIRECTORY_SORT_KEY = "gbc:reports:directory-sort";
// 列表视图的列：名称、最新预算、最新决算、待复核、材料、箭头；窄屏两列排布。
const LIST_COLUMNS = "grid-cols-2 gap-x-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_4.5rem_6rem_1rem]";

/** 目录里一份最新材料的状态徽章，例如“预算 2026 · 待复核”。 */
function LatestMaterialBadge({ kind, status, testId, quietEmpty = false }: { kind: "budget" | "final"; status?: LatestMaterialStatus; testId: string; quietEmpty?: boolean }) {
  const view = directoryMaterialStatus(status);
  const kindLabel = kind === "budget" ? "预算" : "决算";
  // 列表有列标题，没有材料时只写淡色“暂无”；窄屏隐藏列标题时补上材料类型。
  if (!status && quietEmpty) return <span className="text-xs text-slate-400" data-testid={testId}><span className="md:hidden">{kindLabel} · </span>{view.label}</span>;
  return <Badge tone={view.tone} className="max-w-full px-2 py-0.5 font-normal" data-testid={testId}>{kindLabel}{status ? ` ${status.fiscal_year}` : ""} · {view.label}</Badge>;
}

/** 部门卡片看部门汇总口径；单位、部门本级卡片和下属单位目录看单位本级口径。 */
function latestMaterials(status: OrganizationReviewStatus | undefined, unitScope: boolean) {
  const scope = unitScope ? "unit_self" : "department_summary";
  return { budget: status?.latest[`${scope}:budget`], final: status?.latest[`${scope}:final`] };
}

/**
 * 列表查询只由视图与首屏筛选决定；地址还原与页面状态共用同一构造，同一视图不会重复取数。
 * 年度卡片一次取全部年度，切换年度在本地完成。
 */
function buildReportQuery(view: ReportViewState, organizationDetail: boolean, cardView: boolean, filters: { year: string; kind: ReportKindFilter; keyword: string; reviewStatus: string; page: number }): DepartmentReportQuery {
  return {
    organizationId: view.organizationId, includeDescendants: view.includeDescendants, year: filters.year, kind: filters.kind, keyword: filters.keyword, reviewStatus: filters.reviewStatus, page: filters.page, pageSize: cardView ? CARD_PAGE_SIZE : REPORT_PAGE_SIZE,
    reportCategory: organizationDetail ? view.reportTab : "all",
    managementScope: view.organizationId === "unassigned" ? "all" : view.managementScope,
    managementRegionId: view.organizationId === "unassigned" ? "" : view.regionId,
  };
}

/**
 * 不带组织时是部门目录首页，只做查找与进入；带组织时是独立地址的部门、单位、地区汇总或待确认归属材料页。
 * 历史报告走上传任务的授权接口，未入材料槽位的文件也能按组织与财政年度查到。
 */
export function DepartmentReportsPage({ routeOrganizationId }: { routeOrganizationId?: string } = {}) {
  const router = useRouter();
  const detailMode = Boolean(routeOrganizationId);
  const [reportView, setReportView] = useState<MaterialReportPage | null>(null);
  const [organizations, setOrganizations] = useState<OrganizationFilterOption[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [year, setYear] = useState("");
  const [kind, setKind] = useState<ReportKindFilter>("all");
  const [keyword, setKeyword] = useState("");
  const [reviewStatus, setReviewStatus] = useState("all");
  const [orgKeyword, setOrgKeyword] = useState("");
  const [directoryId, setDirectoryId] = useState("");
  const [regionId, setRegionId] = useState("");
  const [managementScope, setManagementScope] = useState<ReportManagementScope>("city");
  const [directoryView, setDirectoryView] = useState<"grid" | "list">("grid");
  const [directorySort, setDirectorySort] = useState<DirectorySort>("attention");
  const [directoryPageSize, setDirectoryPageSize] = useState<number>(DIRECTORY_PAGE_SIZE);
  const [isAdmin, setIsAdmin] = useState(false);
  const [loadedQueryKey, setLoadedQueryKey] = useState("");
  const [directoryPage, setDirectoryPage] = useState(0);
  const [reportPage, setReportPage] = useState(0);
  const [cardYear, setCardYear] = useState("");
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [reportTab, setReportTab] = useState<"department" | "units">("department");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [routeStatus, setRouteStatus] = useState<"pending" | "ready" | "missing">("pending");
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // 切换材料口径时从标题开始，避免沿用长目录的滚动位置。
    pageRef.current?.scrollIntoView({ block: "start" });
  }, [organizationId, reportTab]);
  useEffect(() => {
    // 旧版驾驶舱链接与书签（/reports?organization=）改到部门独立地址。
    const legacyOrganization = detailMode ? null : new URLSearchParams(window.location.search).get("organization");
    if (legacyOrganization) router.replace(reportDetailHref(legacyOrganization) as Route);
  }, [detailMode, router]);
  useEffect(() => {
    // 目录展示方式与排序只是本机显示偏好；存储不可用时使用默认卡片、待复核优先。
    try {
      if (window.localStorage.getItem(DIRECTORY_VIEW_KEY) === "list") setDirectoryView("list");
      if (window.localStorage.getItem(DIRECTORY_SORT_KEY) === "name") setDirectorySort("name");
    } catch { /* 使用默认值。 */ }
  }, []);
  const reportViewRef = useRef<MaterialReportPage | null>(null);
  const selectedOrganization = organizations.find((item) => item.id === organizationId);
  const isDetailView = Boolean(selectedOrganization && !isReportRegion(selectedOrganization));
  const departmentId = useMemo(() => reportDepartmentId(organizations, organizationId), [organizations, organizationId]);
  const reportCounts = isDetailView && reportTab === "units" ? reportView?.organization_unit_report_counts : reportView?.organization_report_counts;
  const directory = useMemo(() => buildReportDirectoryIndex(organizations, reportCounts ?? {}), [organizations, reportCounts]);
  const cardView = reportCardView(directory, { organizationId, includeDescendants, reportTab, managementScope, regionId, directoryId: "" });
  const reportQuery = useMemo<DepartmentReportQuery>(() => buildReportQuery(
    { organizationId, includeDescendants, reportTab, managementScope, regionId, directoryId: "" }, isDetailView, cardView,
    { year, kind, keyword, reviewStatus, page: reportPage + 1 },
  ), [organizationId, includeDescendants, year, kind, keyword, reviewStatus, reportPage, managementScope, regionId, isDetailView, reportTab, cardView]);
  /** 取数总是读最新的筛选；请求期间筛选变化时旧结果作废。 */
  const queryRef = useRef(reportQuery);
  const abortRef = useRef<AbortController | null>(null);
  // 组织地址在同一次挂载内不变：换组织是新地址、重新挂载，同一组织内只改写口径参数。
  const routeOrganizationRef = useRef(routeOrganizationId);
  const routeResolvedRef = useRef(false);
  const favoriteStorageKey = useRef<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    // 收藏按登录用户保存，仅记录组织 ID，不把材料内容写入浏览器存储。
    void (async () => {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        if (!cancelled) setIsAdmin(Boolean(payload.user?.is_admin));
        const username = payload.user?.username;
        if (cancelled || typeof username !== "string" || !username) return;
        const key = `gbc:reports:favorites:${username}`;
        favoriteStorageKey.current = key;
        const saved: unknown = JSON.parse(window.localStorage.getItem(key) ?? "[]");
        if (Array.isArray(saved)) setFavoriteIds(saved.filter((id): id is string => typeof id === "string").slice(0, 200));
      } catch {
        // 存储不可用时仍可在本次访问中收藏，目录与报告加载不受影响。
      }
    })();
    return () => { cancelled = true; };
  }, []);
  /** 换视图时一并清空筛选，新视图从第一页的全部材料开始。 */
  const applyView = useCallback((view: ReportViewState) => {
    setOrganizationId(view.organizationId); setIncludeDescendants(view.includeDescendants); setReportTab(view.reportTab);
    setManagementScope(view.managementScope); setRegionId(view.regionId); setDirectoryId(view.directoryId);
    setYear(""); setKind("all"); setKeyword(""); setReviewStatus("all"); setReportPage(0);
    setOrgKeyword(""); setFavoritesOnly(false); setDirectoryPage(0); setCardYear("");
  }, []);
  const load = useCallback(async () => {
    try {
      const orgResponse = await fetch("/api/organizations?stats=none", { cache: "no-store" });
      if (!orgResponse.ok) throw new Error(`组织目录加载失败（HTTP ${orgResponse.status}），请刷新重试。`);
      const nextOrganizations = flattenOrganizationTree((await orgResponse.json()).tree);
      let resolved: ReturnType<typeof resolveReportView> | null = null;
      let routeSearch = new URLSearchParams();
      if (!routeResolvedRef.current) {
        // 首次加载先按地址还原视图，再按该视图取数，不先取默认范围再重取。
        const index = buildReportDirectoryIndex(nextOrganizations, {});
        routeSearch = new URLSearchParams(window.location.search);
        resolved = resolveReportView(index, routeOrganizationRef.current, routeSearch);
        if (resolved.status === "ready") {
          const selected = index.byId.get(resolved.view.organizationId);
          queryRef.current = buildReportQuery(resolved.view, Boolean(selected && !isReportRegion(selected)), reportCardView(index, resolved.view), { year: "", kind: "all", keyword: "", reviewStatus: "all", page: 1 });
        }
      }
      // 只取当前一页报告与各组织报告数，不再下载全部任务摘要。
      let next: MaterialReportPage;
      for (;;) {
        const query = queryRef.current;
        const controller = new AbortController();
        abortRef.current = controller;
        try {
          const params = new URLSearchParams({ org: query.organizationId, org_scope: query.includeDescendants ? "descendants" : "exact", year: query.year, kind: query.kind, q: query.keyword, review: query.reviewStatus ?? "all", page: String(query.page), page_size: String(query.pageSize), management_scope: query.managementScope ?? "all", management_region: query.managementRegionId ?? "", report_category: query.reportCategory ?? "all" });
          const response = await fetch(`/api/materials/reports?${params}`, { cache: "no-store", signal: controller.signal });
          if (!response.ok) throw new Error(`材料报告加载失败（HTTP ${response.status}），请刷新重试。`);
          const payload = await response.json();
          if (!payload.ok || !Array.isArray(payload.data?.items)) throw new Error("材料报告响应无效，请刷新重试。");
          next = payload.data;
          if (query !== queryRef.current) continue;
          if (next.page !== query.page) {
            // 服务端把超出范围的页码收敛到了最后一页：本地页码跟上，且不再触发重复请求。
            queryRef.current = { ...query, page: next.page };
            setReportPage(next.page - 1);
          }
          setLoadedQueryKey(JSON.stringify(queryRef.current));
          break;
        } catch (reason) {
          if (controller.signal.aborted && query !== queryRef.current) continue;
          throw reason;
        } finally {
          if (abortRef.current === controller) abortRef.current = null;
        }
      }
      reportViewRef.current = next;
      setReportView(next);
      setOrganizations(nextOrganizations);
      if (resolved) {
        routeResolvedRef.current = true;
        if (resolved.status === "ready") {
          applyView(resolved.view);
          // 年度卡片选中的财政年度；不在本主体年度中时显示最新年度。
          setCardYear(routeSearch.get("year") ?? "");
        }
        setRouteStatus(resolved.status);
      } else {
        const knownIds = new Set(nextOrganizations.map((item) => item.id));
        setDirectoryId((current) => knownIds.has(current) ? current : "");
        setRegionId((current) => knownIds.has(current) ? current : "");
        setOrganizationId((current) => current === "unassigned" || knownIds.has(current) ? current : "");
        const routeOrganization = routeOrganizationRef.current;
        // 刷新后组织不再可见（调整或收回授权）时不改看其他范围，直接提示。
        if (routeOrganization && routeOrganization !== "unassigned" && !knownIds.has(routeOrganization)) setRouteStatus("missing");
      }
      setLoaded(true);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "报告加载失败，请刷新重试。");
      throw reason;
    }
  }, [applyView]);
  const polling = useJobPolling({ fetcher: load, decide: useCallback(() => resolvePollingDecisionFromActivity(reportViewRef.current?.activity), []) });
  const refreshReports = polling.refreshNow;
  useEffect(() => {
    const refresh = () => { if (!document.hidden) refreshReports(); };
    window.addEventListener("focus", refresh);
    window.addEventListener("govbudgetchecker:material-reports-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("govbudgetchecker:material-reports-changed", refresh);
    };
  }, [refreshReports]);
  // 筛选或翻页后重新取数；关键词输入稍作停顿再请求。地址还原前不发起默认范围的请求。
  const lastKeywordRef = useRef(keyword);
  useEffect(() => {
    if (!routeResolvedRef.current) return;
    const current = queryRef.current;
    if (Object.keys(reportQuery).every((key) => current[key as keyof DepartmentReportQuery] === reportQuery[key as keyof DepartmentReportQuery])) return;
    const delay = keyword !== lastKeywordRef.current ? 300 : 0;
    lastKeywordRef.current = keyword;
    const timer = window.setTimeout(() => {
      queryRef.current = reportQuery;
      abortRef.current?.abort();
      refreshReports();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [reportQuery, keyword, refreshReports]);
  const favorites = useMemo(() => new Set(favoriteIds), [favoriteIds]);
  const scopedDirectory = useMemo(() => reportManagementDirectory(directory, managementScope, isDetailView ? departmentId || organizationId : regionId), [directory, managementScope, regionId, isDetailView, departmentId, organizationId]);
  const filteredDirectoryItems = useMemo(() => reportDirectoryItems(scopedDirectory, { parentId: directoryId, regionId: isDetailView ? departmentId || organizationId : regionId, keyword: orgKeyword, favoritesOnly, favoriteIds: favorites }).filter((item) => {
    // 本级入口仅代表真实登记的单位口径材料，不能把部门汇总数搬到单位卡片上。
    return !isDetailView || item.id !== departmentId || (scopedDirectory.stats.get(item.id)?.ownReportCount ?? 0) > 0;
  }), [scopedDirectory, directoryId, regionId, orgKeyword, favoritesOnly, favorites, isDetailView, departmentId, organizationId]);
  // 待复核数与材料数同口径：下属单位目录只计单位本级材料。
  const unitScopeCounts = isDetailView && reportTab === "units";
  const reviewStatusByOrganization = reportView?.organization_review_status;
  const openTotals = useMemo(() => aggregateDirectoryCounts(scopedDirectory, Object.fromEntries(Object.entries(reviewStatusByOrganization ?? {})
    .map(([id, status]) => [id, openReviewCount(unitScopeCounts ? status.unit_review_counts : status.review_counts)]))), [scopedDirectory, reviewStatusByOrganization, unitScopeCounts]);
  const pinnedDirectoryId = !orgKeyword.trim() && !favoritesOnly ? directoryId : "";
  const directoryItems = useMemo(() => sortDirectoryItems(filteredDirectoryItems, directorySort, (id) => ({
    open: openTotals.get(id)?.total ?? 0, materials: scopedDirectory.stats.get(id)?.reportCount ?? 0,
  }), pinnedDirectoryId), [filteredDirectoryItems, directorySort, openTotals, scopedDirectory, pinnedDirectoryId]);
  const districtOptions = organizations.filter((item) => item.level === "district");
  const cityRegions = directory.regions.filter((item) => item.level === "city");
  const choosingDistrict = !detailMode && managementScope === "district" && !districtOptions.some((item) => item.id === regionId);
  const districtCards = isAdmin ? [
    ...SHANGHAI_DISTRICTS.map((name) => ({ name, organization: districtOptions.find((item) => item.name.replace(/^上海市/, "") === name) })),
    ...districtOptions.filter((item) => !SHANGHAI_DISTRICTS.some((name) => item.name.replace(/^上海市/, "") === name)).map((item) => ({ name: item.name, organization: item })),
  ] : districtOptions.map((item) => ({ name: item.name, organization: item }));
  const reportsCurrent = loadedQueryKey === JSON.stringify(reportQuery);
  const activeReportView = reportsCurrent ? reportView : null;
  const loadedQuery = useMemo(() => loadedQueryKey ? JSON.parse(loadedQueryKey) as DepartmentReportQuery : null, [loadedQueryKey]);
  const countsCurrent = loadedQuery?.managementScope === reportQuery.managementScope && loadedQuery?.managementRegionId === reportQuery.managementRegionId;
  const yearsCurrent = countsCurrent && loadedQuery?.organizationId === organizationId && loadedQuery?.includeDescendants === includeDescendants && loadedQuery?.reportCategory === reportQuery.reportCategory;
  const directoryPageCount = Math.max(1, Math.ceil(directoryItems.length / directoryPageSize));
  const currentDirectoryPage = Math.min(directoryPage, directoryPageCount - 1);
  const currentOrganization = directory.byId.get(organizationId);
  const directoryOrganization = directory.byId.get(directoryId);
  // 年份选项只受组织范围影响（接口按组织范围统计），不随年份/类型/关键词收窄。
  const years = useMemo(() => reportView && yearsCurrent
    ? [...reportView.years.map(String), ...(reportView.has_unknown_year ? ["unknown"] : [])]
    : [], [reportView, yearsCurrent]);
  const visibleTotal = activeReportView?.total;
  const reportPageCount = Math.max(1, activeReportView?.page_count ?? 1);
  const currentReportPage = Math.max(0, (activeReportView?.page ?? 1) - 1);
  const groups = useMemo(() => groupReportsByFiscalYear(activeReportView?.items ?? []), [activeReportView]);
  const scopeName = managementScope === "city" ? "市级部门" : directory.byId.get(regionId)?.name ?? "区级部门";
  const selectedName = organizationId === "unassigned" ? "待确认归属（当前账号授权范围）" : isDetailView && currentOrganization ? currentOrganization.name : `${scopeName} · 全部材料`;
  const directoryHref = reportDirectoryHref(directory, managementScope, regionId);
  /** 单位地址本身就是单位口径，不再附带口径参数。 */
  const detailHref = (id: string, view: { units?: boolean; onlySelf?: boolean; year?: string } = {}) => reportDetailHref(id, directory.byId.get(id)?.level === "unit" ? { year: view.year } : view);
  // 年度卡片：地址指定的年度不在本主体年度中时显示最新年度。
  const selectedCardYear = cardView && years.length ? years.includes(cardYear) ? cardYear : years[0] : "";
  const cardItems = useMemo(() => {
    const inYear = (activeReportView?.items ?? []).filter((item) => String(item.fiscal_year ?? "unknown") === selectedCardYear);
    return { budget: inYear.filter((item) => item.report_kind === "budget"), final: inYear.filter((item) => item.report_kind === "final"), other: inYear.filter((item) => item.report_kind !== "budget" && item.report_kind !== "final") };
  }, [activeReportView, selectedCardYear]);
  const viewHref = (cardYearValue: string) => detailHref(organizationId, { onlySelf: isDetailView && !includeDescendants, units: isDetailView && reportTab === "units", year: cardView ? cardYearValue || undefined : undefined });
  const currentHref = organizationId === "unassigned" ? "/reports/unassigned" : organizationId ? viewHref(selectedCardYear) : "/reports";
  const selectCardYear = (value: string) => {
    setCardYear(value);
    window.history.replaceState(null, "", viewHref(value));
  };
  /** 换组织进入新地址并保留后退；同一组织内切换口径只改写当前地址。 */
  const openView = (id: string, view: { units?: boolean; onlySelf?: boolean } = {}) => {
    const href = detailHref(id, view);
    if (id !== routeOrganizationId) {
      router.push(href as Route);
      return;
    }
    window.history.replaceState(null, "", href);
    applyView(reportViewForOrganization(directory, id, view));
  };
  /** 目录首页的范围与所选地区写进地址，从部门返回时回到同一范围。 */
  const showDirectory = (view: ReportViewState) => {
    window.history.replaceState(null, "", reportDirectoryHref(directory, view.managementScope, view.regionId));
    applyView(view);
  };
  const switchManagementScope = (scope: ReportManagementScope) => {
    const rootId = scope === "city" ? organizations.find((item) => item.level === "city")?.id ?? "" : "";
    showDirectory({ organizationId: rootId, includeDescendants: true, reportTab: "department", managementScope: scope, regionId: rootId, directoryId: rootId });
  };
  const chooseRegion = (id: string) => id ? showDirectory(reportViewForOrganization(directory, id)) : switchManagementScope(managementScope);
  const openDirectoryItem = (item: OrganizationFilterOption, ownCard: boolean) => {
    if (detailMode) openView(item.id, { onlySelf: ownCard });
    else if (isReportRegion(item)) chooseRegion(item.id);
    else router.push(detailHref(item.id) as Route);
  };
  const switchReportTab = (tab: "department" | "units") => openView(departmentId || organizationId, { units: tab === "units" });
  const toggleDirectoryView = (view: "grid" | "list") => {
    setDirectoryView(view);
    try { window.localStorage.setItem(DIRECTORY_VIEW_KEY, view); } catch { /* 仅本次访问生效。 */ }
  };
  const changeDirectorySort = (sort: DirectorySort) => {
    setDirectorySort(sort); setDirectoryPage(0);
    try { window.localStorage.setItem(DIRECTORY_SORT_KEY, sort); } catch { /* 仅本次访问生效。 */ }
  };
  const toggleFavorite = (id: string) => {
    const next = favorites.has(id) ? favoriteIds.filter((value) => value !== id) : [...favoriteIds, id].slice(-200);
    setFavoriteIds(next);
    if (favoriteStorageKey.current) {
      try { window.localStorage.setItem(favoriteStorageKey.current, JSON.stringify(next)); } catch { /* 保留本次访问中的收藏。 */ }
    }
  };
  const filterClass = "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50";
  const toggleClass = (active: boolean) => `${filterClass} ${active ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`;
  const directoryTitle = orgKeyword.trim() ? "搜索结果" : favoritesOnly ? "收藏目录" : directoryOrganization && !isReportRegion(directoryOrganization) ? "下属单位（含部门本级）" : "部门与单位目录";
  const scopeRootId = !detailMode && regionId ? regionId : "";
  const unassignedTotal = reportView?.unassigned_total ?? 0;
  const reviewTotals = activeReportView?.review_status_counts;
  const refreshButton = <button type="button" onClick={polling.refreshNow} disabled={polling.isManualRefreshing} className={`${filterClass} inline-flex items-center gap-1.5`} data-testid="gbc-reports-refresh"><RefreshCw className={`h-4 w-4 ${polling.isManualRefreshing ? "animate-spin" : ""}`} aria-hidden="true" />{polling.isManualRefreshing ? "刷新中…" : "刷新"}</button>;

  const directoryContent = <div className="mt-4" data-testid="gbc-reports-unit-directory">
        {!detailMode && managementScope === "district" ? <div className="mb-3 flex flex-wrap items-center gap-1 text-sm" data-testid="gbc-reports-management-summary">
          <button type="button" onClick={() => switchManagementScope("district")} className="inline-flex items-center gap-1 rounded px-1 py-1 text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-back-to-districts"><ChevronLeft className="h-4 w-4" aria-hidden="true" />全部区</button>
          <span className="text-slate-400" aria-hidden="true">/</span>
          <span className="font-medium text-slate-800">{scopeName}</span>
        </div> : null}
        <div className="flex flex-wrap items-center gap-2">
          {!detailMode && managementScope === "city" && cityRegions.length > 1 ? <select aria-label="选择报告所属地区" value={regionId} onChange={(event) => chooseRegion(event.target.value)} className={`${filterClass} w-full sm:w-56`} data-testid="gbc-reports-region">
            <option value="">全部市级部门</option>
            {cityRegions.map((item) => <option key={item.id} value={item.id}>{`${item.name} · 市级部门`}</option>)}
          </select> : null}
          <div className="relative min-w-0 flex-1 basis-full sm:basis-60">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <input type="search" aria-label="搜索部门或单位" placeholder={detailMode ? "搜索本部门下属单位" : `在${scopeName}范围内搜索部门或下属单位`} value={orgKeyword} onChange={(event) => { setOrgKeyword(event.target.value); setDirectoryPage(0); }} className={`${filterClass} w-full pl-9`} data-testid="gbc-reports-org-search" />
          </div>
          <div className="flex gap-1" role="group" aria-label="目录展示范围">
            <button type="button" onClick={() => { setFavoritesOnly(false); setDirectoryPage(0); }} aria-pressed={!favoritesOnly} className={toggleClass(!favoritesOnly)} data-testid="gbc-reports-directory-all">全部</button>
            <button type="button" onClick={() => { setFavoritesOnly(true); setDirectoryPage(0); }} aria-pressed={favoritesOnly} className={`${toggleClass(favoritesOnly)} inline-flex items-center gap-1`} data-testid="gbc-reports-directory-favorites"><Star className="h-3.5 w-3.5" aria-hidden="true" />收藏</button>
          </div>
          <select aria-label="目录排序" value={directorySort} onChange={(event) => changeDirectorySort(event.target.value as DirectorySort)} className={filterClass} data-testid="gbc-reports-directory-sort">
            <option value="attention">待复核优先</option>
            <option value="name">按名称</option>
          </select>
          <div className="flex gap-1" role="group" aria-label="目录展示方式">
            {([["grid", "卡片", LayoutGrid], ["list", "列表", List]] as const).map(([value, label, Icon]) => <button key={value} type="button" onClick={() => toggleDirectoryView(value)} aria-pressed={directoryView === value} aria-label={label} title={label} className={`${toggleClass(directoryView === value)} px-2.5`} data-testid={`gbc-reports-view-${value}`}><Icon className="h-4 w-4" aria-hidden="true" /></button>)}
          </div>
        </div>
        <div className="mb-3 mt-4 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-700" aria-live="polite">{directoryTitle} <span className="font-normal text-slate-500">· {directoryItems.length} 项</span></h2>
          {scopeRootId ? <Link href={detailHref(scopeRootId) as Route} className="inline-flex items-center gap-1 rounded text-sm text-primary-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-org-all">查看本范围全部材料{reportsCurrent && typeof visibleTotal === "number" ? `（${visibleTotal}）` : ""}<ChevronRight className="h-4 w-4" aria-hidden="true" /></Link> : null}
        </div>
        <div className={directoryView === "grid" ? "grid grid-cols-1 items-start gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" : "rounded-card border border-border bg-white px-3"} data-testid="gbc-reports-directory-grid" data-view={directoryView}>
          {directoryView === "list" && directoryItems.length > 0 ? <div className={`${LIST_COLUMNS} hidden border-b border-border py-2 text-xs text-slate-500 md:grid`} aria-hidden="true"><span className="pl-6">部门 / 单位</span><span>最新预算</span><span>最新决算</span><span className="text-right">待复核</span><span className="text-right">材料</span><span /></div> : null}
          {directoryItems.slice(currentDirectoryPage * directoryPageSize, (currentDirectoryPage + 1) * directoryPageSize).map((item) => {
            const ownCard = item.id === directoryId && !isReportRegion(item) && !orgKeyword.trim() && !favoritesOnly;
            const stats = scopedDirectory.stats.get(item.id)!;
            const childCount = directory.children.get(item.id)?.length ?? 0;
            const selectedCard = organizationId === item.id && (!ownCard || !includeDescendants);
            const materials = countsCurrent ? ownCard ? stats.ownReportCount : stats.reportCount : null;
            const open = countsCurrent ? (ownCard ? openTotals.get(item.id)?.own : openTotals.get(item.id)?.total) ?? 0 : null;
            const latest = latestMaterials(reviewStatusByOrganization?.[item.id], ownCard || unitScopeCounts || item.level === "unit");
            // 地区是目录入口，没有自己的预算决算；旧接口不返回状态时也不显示“暂无”。
            const showLatest = !isReportRegion(item) && Boolean(reviewStatusByOrganization) && countsCurrent;
            const budgetBadge = <LatestMaterialBadge kind="budget" status={latest.budget} testId={`gbc-reports-org-${item.id}-budget`} quietEmpty={directoryView === "list"} />;
            const finalBadge = <LatestMaterialBadge kind="final" status={latest.final} testId={`gbc-reports-org-${item.id}-final`} quietEmpty={directoryView === "list"} />;
            const name = <span className="block break-words text-sm font-medium text-slate-900">{ownCard ? `${item.name}（本级）` : item.name}{favorites.has(item.id) ? <Star className="ml-1 inline h-3.5 w-3.5 text-primary-700" aria-label="已收藏" /> : null}</span>;
            const level = <span>{ownCard ? "本级" : `${reportOrganizationLevelLabel(item)}${stats.unitCount ? ` · ${stats.unitCount} 个下属单位` : childCount ? ` · ${childCount} 个下级组织` : ""}`}</span>;
            const path = orgKeyword.trim() || favoritesOnly ? <span className="mt-1 block break-words text-xs text-slate-500">{reportDirectoryPath(directory, item.id)}</span> : null;
            const icon = <span className="mt-0.5 shrink-0 text-primary-700">{isReportRegion(item) ? <FolderOpen className="h-4 w-4" aria-hidden="true" /> : <Building2 className="h-4 w-4" aria-hidden="true" />}</span>;
            const stateClass = selectedCard ? "border-primary-400 bg-primary-50" : "border-border bg-white hover:border-primary-300 hover:bg-primary-50";
            const focusClass = "text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400";
            if (directoryView === "list") {
              return <button key={item.id} type="button" onClick={() => openDirectoryItem(item, ownCard)} aria-pressed={selectedCard} data-testid={`gbc-reports-org-${item.id}`}
                className={`${LIST_COLUMNS} grid w-full min-w-0 items-center gap-y-1.5 border-b py-2.5 last:border-b-0 ${focusClass} ${stateClass}`}>
                <span className="col-span-2 flex min-w-0 items-start gap-2 md:col-span-1">{icon}<span className="min-w-0">{name}<span className="mt-0.5 block text-xs text-slate-500">{level}</span>{path}</span></span>
                {showLatest ? <><span className="min-w-0 pl-6 md:pl-0">{budgetBadge}</span><span className="min-w-0">{finalBadge}</span></> : <><span className="hidden md:block" /><span className="hidden md:block" /></>}
                <span className={`pl-6 text-xs md:pl-0 md:text-right md:text-sm ${open ? "font-medium text-warning-700" : "text-slate-500"}`}><span className="md:hidden">待复核 </span>{open ?? "—"}</span>
                <span className="text-xs text-slate-500 md:text-right md:text-sm">{materials ?? "—"} 份材料</span>
                <ChevronRight className="hidden h-4 w-4 text-slate-400 md:block" aria-hidden="true" />
              </button>;
            }
            return <button key={item.id} type="button" onClick={() => openDirectoryItem(item, ownCard)} aria-pressed={selectedCard} data-testid={`gbc-reports-org-${item.id}`}
              className={`flex min-w-0 items-start gap-2 rounded-card border px-3 py-2.5 ${focusClass} ${stateClass}`}>
              {icon}
              <span className="min-w-0 flex-1">
                {name}
                <span className="mt-1 flex flex-wrap justify-between gap-x-2 text-xs text-slate-500">{level}<span>{materials ?? "—"} 份材料</span></span>
                {showLatest && (latest.budget || latest.final || open) ? <span className="mt-2 flex flex-wrap items-center gap-1.5">
                  {latest.budget || latest.final ? <>{budgetBadge}{finalBadge}</> : null}
                  {open ? <span className="text-xs font-medium text-warning-700" data-testid={`gbc-reports-org-${item.id}-open`}>{open} 份待复核</span> : null}
                </span> : null}
                {path}
              </span>
              <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
            </button>;
          })}
        </div>
        {loaded && directoryItems.length === 0 ? <p role="status" className="rounded-card border border-dashed border-border bg-white p-6 text-center text-sm text-slate-500">{orgKeyword.trim() ? "没有找到匹配的部门或单位，可以缩短关键词或切换到全部地区。" : favoritesOnly ? "当前地区还没有收藏，进入部门或单位后点击标题旁的星标即可收藏。" : "当前目录没有下级组织。"}</p> : null}
        {directoryItems.length > 0 ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500" data-testid="gbc-reports-directory-pagination">
          <span>共 {directoryItems.length} 项 · 当前 {currentDirectoryPage * directoryPageSize + 1}–{Math.min((currentDirectoryPage + 1) * directoryPageSize, directoryItems.length)} 项 · 第 {currentDirectoryPage + 1} / {directoryPageCount} 页</span>
          <div className="flex flex-wrap items-center gap-2"><label>每页 <select aria-label="每页目录数量" value={directoryPageSize} onChange={(event) => { setDirectoryPageSize(Number(event.target.value)); setDirectoryPage(0); }} className={filterClass} data-testid="gbc-reports-directory-page-size">{DIRECTORY_PAGE_SIZES.map((size) => <option key={size} value={size}>{size} 项</option>)}</select></label><button type="button" aria-label="目录上一页" disabled={currentDirectoryPage === 0} onClick={() => setDirectoryPage(currentDirectoryPage - 1)} className={filterClass}><ChevronLeft className="h-4 w-4" aria-hidden="true" /></button><button type="button" aria-label="目录下一页" disabled={currentDirectoryPage + 1 >= directoryPageCount} onClick={() => setDirectoryPage(currentDirectoryPage + 1)} className={filterClass}><ChevronRight className="h-4 w-4" aria-hidden="true" /></button></div>
        </div> : null}
  </div>;

  return (
    <div ref={pageRef} className="mx-auto max-w-[1800px] p-6" data-testid="gbc-department-reports-page">
      {!detailMode ? <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-slate-900">部门报告</h1>
          <p className="mt-1 text-sm text-slate-500">按市级、区级管理范围查找部门与单位，进入部门后按财政年度查看预算和决算。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href={"/history" as Route} className="rounded px-2 py-2 text-sm text-slate-500 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">任务历史</Link>
          <Link href={"/upload" as Route} className={filterClass}>上传新报告</Link>
          {refreshButton}
        </div>
      </div> : null}
      {error ? <p role="alert" className="mt-4 rounded-md bg-danger-50 p-3 text-sm text-danger-700">{error}{loaded ? " 当前保留上次成功加载的结果。" : ""}</p> : null}
      {!loaded && !error ? <p className="mt-4 text-sm text-slate-500">正在加载部门报告…</p> : null}
      {!detailMode ? <section className="mt-5" aria-label="部门与单位分级目录" data-testid="gbc-reports-directory">
        <div className="flex border-b border-border" role="group" aria-label="报告管理范围">
          {/* 首次加载按地址还原范围，完成前不接受切换，避免还原结果覆盖用户的选择。 */}
          {([["city", "市级部门"], ["district", "区级部门"]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => switchManagementScope(value)} disabled={!loaded} aria-pressed={managementScope === value} className={`border-b-2 px-5 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-wait ${managementScope === value ? "border-primary-600 text-primary-700" : "border-transparent text-slate-500 hover:text-primary-700"}`} data-testid={`gbc-reports-management-${value}`}>{label}</button>)}
        </div>
        {unassignedTotal > 0 ? <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning-200 bg-warning-50 px-4 py-2.5 text-sm text-warning-800" data-testid="gbc-reports-unassigned-alert">
          <span className="inline-flex items-center gap-2"><AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />{unassignedTotal} 份材料尚未确认所属部门或材料口径，不在下方目录中。</span>
          <Link href={"/reports/unassigned" as Route} className="rounded font-medium underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-org-unassigned">查看待确认材料</Link>
        </div> : null}
        {!choosingDistrict && reportView?.review_status_counts ? <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="gbc-reports-summary">
          <Metric label="已登记材料" value={activeReportView?.material_total} desc={`${scopeName}范围内`} data-testid="gbc-reports-summary-materials" />
          <Metric label="待复核" value={reviewTotals ? openReviewCount(reviewTotals) : null} tone="warning" desc={reviewTotals && (reviewTotals.in_progress || reviewTotals.invalidated) ? `含复核中 ${reviewTotals.in_progress ?? 0}、需重新复核 ${reviewTotals.invalidated ?? 0}` : "等待复核人处理"} data-testid="gbc-reports-summary-open" />
          <Metric label="已复核" value={reviewTotals ? reviewTotals.completed ?? 0 : null} tone="success" desc="复核结论已提交" data-testid="gbc-reports-summary-completed" />
          <Metric label="等待分析" value={reviewTotals ? reviewTotals.waiting_analysis ?? 0 : null} desc="分析完成后才能复核" data-testid="gbc-reports-summary-waiting" />
        </div> : null}
        {choosingDistrict ? <div className="mt-4" data-testid="gbc-reports-district-picker">
          <h2 className="mb-1 text-sm font-semibold text-slate-800">选择管理区</h2>
          <p className="mb-4 text-xs text-slate-500">选择区 → 选择部门 → 查看单位与材料。未配置组织目录的区暂不可进入。</p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {districtCards.map(({ name, organization }) => <button key={organization?.id ?? name} type="button" disabled={!organization} onClick={() => organization && chooseRegion(organization.id)} className="flex min-w-0 items-center justify-between gap-2 rounded-card border border-border bg-white px-4 py-4 text-left text-sm font-medium text-slate-800 hover:border-primary-300 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:bg-slate-50 disabled:text-slate-400 disabled:hover:border-border disabled:hover:bg-slate-50" data-testid={`gbc-reports-district-${organization?.id ?? name}`}><span className="break-words">{name}</span>{organization ? <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" /> : <span className="text-xs font-normal">未配置</span>}</button>)}
          </div>
          {loaded && districtCards.length === 0 ? <p className="mt-4 text-sm text-slate-500">当前账号没有可访问的区级组织目录。</p> : null}
        </div> : directoryContent}
      </section> : null}
      {detailMode && loaded && routeStatus === "missing" ? <div role="status" className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500" data-testid="gbc-reports-missing">
        未找到这个部门或单位：它可能已调整，或当前账号没有访问权限。
        <div className="mt-3"><Link href={"/reports" as Route} className="text-primary-700 underline" data-testid="gbc-reports-back-directory">返回部门目录</Link></div>
      </div> : null}
      {detailMode && routeStatus === "ready" ? <section className="min-w-0" aria-label="当前部门与单位的报告" data-testid="gbc-reports-selected">
          <div className="mb-4 flex items-center justify-between gap-3">
            <Link href={directoryHref as Route} className={`${filterClass} inline-flex items-center gap-1`} data-testid="gbc-reports-back-directory"><ChevronLeft className="h-4 w-4" aria-hidden="true" />返回部门目录</Link>
            {refreshButton}
          </div>
          {currentOrganization ? <nav aria-label="当前位置" className="mb-2 break-words text-xs text-slate-500" data-testid="gbc-reports-selected-path">
            {(directory.paths.get(currentOrganization.id) ?? []).map((item, index, path) => <Fragment key={item.id}>
              {index > 0 ? " › " : null}
              {index === path.length - 1 ? <span aria-current="page" className="text-slate-700">{item.name}</span>
                : <Link href={(isReportRegion(item) ? reportDirectoryHref(directory, reportManagementScope(directory, item.id), item.id) : detailHref(item.id)) as Route} className="text-primary-700 hover:underline">{item.name}</Link>}
            </Fragment>)}
          </nav> : null}
          <div className="flex items-start justify-between gap-3">
            <h1 className="break-words text-xl font-semibold text-slate-900">{selectedName} <span className="text-sm font-normal text-slate-500" aria-live="polite">· {visibleTotal ?? "—"} 条材料记录</span></h1>
            {isDetailView && currentOrganization ? <button type="button" onClick={() => toggleFavorite(currentOrganization.id)} aria-pressed={favorites.has(currentOrganization.id)} aria-label={`${favorites.has(currentOrganization.id) ? "取消收藏" : "收藏"}${currentOrganization.name}`} className={`${toggleClass(favorites.has(currentOrganization.id))} shrink-0`} data-testid="gbc-reports-favorite"><Star className={`h-4 w-4 ${favorites.has(currentOrganization.id) ? "fill-current" : ""}`} aria-hidden="true" /></button> : null}
          </div>
          {organizationId === "unassigned" ? <p className="mt-2 text-xs text-slate-500">这些材料尚未确认所属部门或材料口径，范围为当前账号已有授权。</p> : null}
          {!isDetailView && organizationId !== "unassigned" ? <p className="mt-2 text-xs text-slate-500">本范围内各部门及下属单位的全部材料。</p> : null}
          {isDetailView ? <>
            <div className="mt-4 flex gap-2 border-b border-border" role="tablist" aria-label="材料口径">
              {REPORT_TABS.map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={reportTab === value} onClick={() => switchReportTab(value)} className={`border-b-2 px-5 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${reportTab === value ? "border-primary-600 text-primary-700" : "border-transparent text-slate-500"}`} data-testid={`gbc-reports-tab-${value}`}>{label}</button>)}
            </div>
            <p className="mt-3 text-xs text-slate-500">{reportTab === "department" ? "部门：查看部门汇总口径的预算和决算材料。" : "下属单位：查看部门本级及各下属单位的单位口径材料。"}</p>
            {reportTab === "units" ? directoryContent : null}
            {reportsCurrent && (activeReportView?.uncategorized_total ?? 0) > 0 ? <p className="mt-3 text-xs text-slate-500">另有 {activeReportView?.uncategorized_total} 条材料口径待确认，暂不计入两个标签{regionId ? <>，可在<Link href={detailHref(regionId) as Route} className="text-primary-700 underline">本范围全部材料</Link>中查看</> : null}。</p> : null}
          </> : null}
          {cardView ? <>
            {years.length ? <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="财政年度" data-testid="gbc-reports-year-tabs">
              {years.map((value) => <button key={value} type="button" role="tab" aria-selected={value === selectedCardYear} onClick={() => selectCardYear(value)} className={`${toggleClass(value === selectedCardYear)} ${value === selectedCardYear ? "font-medium" : ""}`} data-testid={`gbc-reports-year-tab-${value}`}>{value === "unknown" ? "年度待确认" : `${value} 年度`}</button>)}
            </div> : null}
            {loaded && !reportsCurrent ? <p className="mt-4 text-sm text-slate-500" role="status">正在加载当前范围的材料…</p> : null}
            {loaded && reportsCurrent && !years.length ? <p className="mt-5 rounded-md border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">这里还没有材料。未关联到部门的文件可在部门目录的“待确认材料”中查看。</p> : null}
            {reportsCurrent && selectedCardYear ? <section className="mt-4" role="tabpanel" aria-label={selectedCardYear === "unknown" ? "年度待确认" : `${selectedCardYear} 年度`} data-testid={`gbc-reports-year-${selectedCardYear}`}>
              <div className="grid gap-3 md:grid-cols-2">
                {(["budget", "final"] as const).map((value) => <div key={value} className="flex min-w-0 flex-col gap-3">
                  {cardItems[value].length ? cardItems[value].map((item) => <MaterialReportCard key={item.item_id} item={item} returnTo={currentHref} subjectId={organizationId} />) : <MaterialReportCardEmpty kind={value} year={selectedCardYear} />}
                </div>)}
              </div>
              {cardItems.other.length ? <>
                <h2 className="mb-2 mt-5 text-sm font-semibold text-slate-700">类型待确认的材料 · {cardItems.other.length} 条</h2>
                <MaterialReportTable items={cardItems.other} returnTo={currentHref} />
              </> : null}
            </section> : null}
            {reportsCurrent && activeReportView && activeReportView.total > activeReportView.items.length ? <p className="mt-3 text-xs text-warning-700">材料较多，这里只按年度显示最近 {activeReportView.items.length} 份{regionId ? <>，全部材料可在<Link href={detailHref(regionId) as Route} className="underline">本范围全部材料</Link>中查看</> : null}。</p> : null}
            {loaded && reportsCurrent ? <p className="mt-4 text-xs text-slate-500">本范围已登记材料 {activeReportView?.material_total ?? "—"} 份 · 未归属记录 {activeReportView?.legacy_total ?? "—"} 条</p> : null}
          </> : <>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select aria-label="按财政年度查看报告" value={year} onChange={(event) => { setYear(event.target.value); setReportPage(0); }} className={filterClass} data-testid="gbc-reports-year">
                <option value="">全部财政年度</option>
                {years.map((value) => <option key={value} value={value}>{value === "unknown" ? "年度待确认" : `${value} 年`}</option>)}
              </select>
              <select aria-label="按复核状态查看材料" value={reviewStatus} onChange={(event) => { setReviewStatus(event.target.value); setReportPage(0); }} className={filterClass}>
                <option value="all">全部复核状态</option><option value="pending">待复核</option><option value="in_progress">复核中</option><option value="completed">已复核</option><option value="invalidated">需重新复核</option><option value="waiting_analysis">等待分析后复核</option><option value="history_only">未归属记录</option>
              </select>
              <input type="search" aria-label="搜索报告" placeholder="搜索报告文件名或单位" value={keyword} onChange={(event) => { setKeyword(event.target.value); setReportPage(0); }} className={`${filterClass} min-w-0 basis-full sm:basis-auto sm:flex-1`} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2" aria-label="按预算或决算查看报告">
              {KINDS.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setReportPage(0); }} data-testid={`gbc-reports-kind-${value}`} className={`${toggleClass(kind === value)} ${kind === value ? "font-medium" : ""}`}>{label}</button>)}
            </div>
            {loaded && !reportsCurrent ? <p className="mt-4 text-sm text-slate-500" role="status">正在加载当前范围的材料…</p> : null}
            {loaded && reportsCurrent && groups.length === 0 ? <p className="mt-5 rounded-md border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">当前筛选下没有报告。可以切换年度或材料类型，未关联组织的文件在部门目录的“待确认材料”中查看。</p> : null}
            {loaded && reportsCurrent ? <p className="mt-3 text-xs text-slate-500">本范围已登记材料 {activeReportView?.material_total ?? "—"} 份 · 未归属记录 {activeReportView?.legacy_total ?? "—"} 条</p> : null}
            {groups.map(([fiscalYear, reports]) => (
              <section key={fiscalYear} className="mt-5" data-testid={`gbc-reports-year-${fiscalYear}`}>
                <h2 className="mb-2 text-sm font-semibold text-slate-700">{fiscalYear === "unknown" ? "财政年度待确认" : `${fiscalYear} 年度`} · {reports.length} 条材料记录{reportPageCount > 1 ? "（本页）" : ""}</h2>
                <MaterialReportTable items={reports} returnTo={currentHref} />
              </section>
            ))}
            {reportPageCount > 1 ? <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500" data-testid="gbc-reports-pagination"><span>共 {visibleTotal} 条材料记录 · 第 {currentReportPage + 1} / {reportPageCount} 页</span><div className="flex gap-2"><button type="button" disabled={currentReportPage === 0} onClick={() => setReportPage(currentReportPage - 1)} className={filterClass}>报告上一页</button><button type="button" disabled={currentReportPage + 1 >= reportPageCount} onClick={() => setReportPage(currentReportPage + 1)} className={filterClass}>报告下一页</button></div></div> : null}
          </>}
      </section> : null}
    </div>
  );
}
