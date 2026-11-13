"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Building2, ChevronLeft, ChevronRight, FolderOpen, Search, Star } from "lucide-react";
import { SectionTitle } from "@/components/ui";
import { resolvePollingDecisionFromActivity } from "@/lib/jobPolling";
import { type MaterialReportPage } from "./materialReportAdapters";
import { MaterialReportTable } from "./MaterialReportTable";
import { flattenOrganizationTree, type OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";
import { useJobPolling } from "../workspace/useJobPolling";
import {
  groupReportsByFiscalYear,
  type DepartmentReportQuery, type ReportKindFilter,
} from "./departmentReportAdapters";
import { buildReportDirectoryIndex, DIRECTORY_PAGE_SIZE, DIRECTORY_PAGE_SIZES, SHANGHAI_DISTRICTS, isReportRegion, REPORT_PAGE_SIZE, reportDirectoryItems, reportDirectoryPath, reportDirectoryRegion, reportOrganizationLevelLabel, reportManagementDirectory, reportManagementScope, type ReportManagementScope } from "./departmentDirectory";

const KINDS: Array<[ReportKindFilter, string]> = [["all", "全部材料"], ["budget", "预算"], ["final", "决算"], ["unknown", "类型待确认"]];
const SCOPES: Array<[boolean, string]> = [[true, "部门及下属单位"], [false, "仅部门本级"]];

/** 历史报告走上传任务的授权接口，未入材料槽位的文件也能按组织与财政年度查到。 */
export function DepartmentReportsPage() {
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
  const [directoryPageSize, setDirectoryPageSize] = useState<number>(DIRECTORY_PAGE_SIZE);
  const [isAdmin, setIsAdmin] = useState(false);
  const [loadedQueryKey, setLoadedQueryKey] = useState("");
  const [directoryPage, setDirectoryPage] = useState(0);
  const [reportPage, setReportPage] = useState(0);
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reportViewRef = useRef<MaterialReportPage | null>(null);
  const reportQuery = useMemo<DepartmentReportQuery>(() => ({
    organizationId, includeDescendants, year, kind, keyword, reviewStatus, page: reportPage + 1, pageSize: REPORT_PAGE_SIZE,
    managementScope: organizationId === "unassigned" ? "all" : managementScope,
    managementRegionId: organizationId === "unassigned" ? "" : regionId,
  }), [organizationId, includeDescendants, year, kind, keyword, reviewStatus, reportPage, managementScope, regionId]);
  /** 取数总是读最新的筛选；请求期间筛选变化时旧结果作废。 */
  const queryRef = useRef(reportQuery);
  const abortRef = useRef<AbortController | null>(null);
  const directoryInitialized = useRef(false);
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
  const load = useCallback(async () => {
    try {
      const orgResponse = await fetch("/api/organizations?stats=none", { cache: "no-store" });
      if (!orgResponse.ok) throw new Error(`组织目录加载失败（HTTP ${orgResponse.status}），请刷新重试。`);
      const nextOrganizations = flattenOrganizationTree((await orgResponse.json()).tree);
      // 只取当前一页报告与各组织报告数，不再下载全部任务摘要。
      let next: MaterialReportPage;
      for (;;) {
        const query = queryRef.current;
        const controller = new AbortController();
        abortRef.current = controller;
        try {
          const params = new URLSearchParams({ org: query.organizationId, org_scope: query.includeDescendants ? "descendants" : "exact", year: query.year, kind: query.kind, q: query.keyword, review: query.reviewStatus ?? "all", page: String(query.page), page_size: String(query.pageSize), management_scope: query.managementScope ?? "all", management_region: query.managementRegionId ?? "" });
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
      const knownIds = new Set(nextOrganizations.map((item) => item.id));
      setDirectoryId((current) => knownIds.has(current) ? current : "");
      setRegionId((current) => knownIds.has(current) ? current : "");
      setOrganizationId((current) => current === "unassigned" || knownIds.has(current) ? current : "");
      setLoaded(true);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "报告加载失败，请刷新重试。");
      throw reason;
    }
  }, []);
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
  // 筛选或翻页后重新取数；关键词输入稍作停顿再请求。
  const lastKeywordRef = useRef(keyword);
  useEffect(() => {
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
  const reportCounts = reportView?.organization_report_counts;
  const directory = useMemo(() => buildReportDirectoryIndex(organizations, reportCounts ?? {}), [organizations, reportCounts]);
  useEffect(() => {
    if (!loaded || directoryInitialized.current) return;
    directoryInitialized.current = true;
    const requestedOrg = new URLSearchParams(window.location.search).get("organization");
    // 只打开授权组织树里存在的目标，驾驶舱链接不能扩大材料可见范围。
    if (requestedOrg && directory.byId.has(requestedOrg)) {
      setDirectoryId(requestedOrg);
      setOrganizationId(requestedOrg);
      setRegionId(reportDirectoryRegion(directory, requestedOrg));
      setManagementScope(reportManagementScope(directory, requestedOrg));
      return;
    }
    const roots = directory.children.get(null) ?? [];
    // 只有一个市级/区级入口时直接进入，省去重复点击“上海市”等唯一目录。
    if (roots.length === 1 && isReportRegion(roots[0])) {
      setDirectoryId(roots[0].id);
      setRegionId(roots[0].id);
      setOrganizationId(roots[0].id);
    }
    const cityDepartments = organizations.some((item) => !isReportRegion(item) && reportManagementScope(directory, item.id) === "city");
    const districts = organizations.filter((item) => item.level === "district");
    if (!cityDepartments && districts.length > 0) {
      setManagementScope("district");
      if (districts.length === 1) {
        setDirectoryId(districts[0].id); setRegionId(districts[0].id); setOrganizationId(districts[0].id);
      } else {
        setDirectoryId(""); setRegionId(""); setOrganizationId("");
      }
    }
  }, [directory, loaded, organizations]);
  const favorites = useMemo(() => new Set(favoriteIds), [favoriteIds]);
  const scopedDirectory = useMemo(() => reportManagementDirectory(directory, managementScope, regionId), [directory, managementScope, regionId]);
  const directoryItems = useMemo(() => reportDirectoryItems(scopedDirectory, { parentId: directoryId, regionId, keyword: orgKeyword, favoritesOnly, favoriteIds: favorites }), [scopedDirectory, directoryId, regionId, orgKeyword, favoritesOnly, favorites]);
  const districtOptions = organizations.filter((item) => item.level === "district");
  const choosingDistrict = managementScope === "district" && !districtOptions.some((item) => item.id === regionId);
  const districtCards = isAdmin ? [
    ...SHANGHAI_DISTRICTS.map((name) => ({ name, organization: districtOptions.find((item) => item.name.replace(/^上海市/, "") === name) })),
    ...districtOptions.filter((item) => !SHANGHAI_DISTRICTS.some((name) => item.name.replace(/^上海市/, "") === name)).map((item) => ({ name: item.name, organization: item })),
  ] : districtOptions.map((item) => ({ name: item.name, organization: item }));
  const reportsCurrent = loadedQueryKey === JSON.stringify(reportQuery);
  const activeReportView = reportsCurrent ? reportView : null;
  const loadedQuery = useMemo(() => loadedQueryKey ? JSON.parse(loadedQueryKey) as DepartmentReportQuery : null, [loadedQueryKey]);
  const countsCurrent = loadedQuery?.managementScope === reportQuery.managementScope && loadedQuery?.managementRegionId === reportQuery.managementRegionId;
  const yearsCurrent = countsCurrent && loadedQuery?.organizationId === organizationId && loadedQuery?.includeDescendants === includeDescendants;
  const directoryPageCount = Math.max(1, Math.ceil(directoryItems.length / directoryPageSize));
  const currentDirectoryPage = Math.min(directoryPage, directoryPageCount - 1);
  const currentOrganization = directory.byId.get(organizationId);
  const directoryOrganization = directory.byId.get(directoryId);
  const directoryPath = directory.paths.get(directoryId) ?? [];
  // 年份选项只受组织范围影响（接口按组织范围统计），不随年份/类型/关键词收窄。
  const years = useMemo(() => reportView && yearsCurrent
    ? [...reportView.years.map(String), ...(reportView.has_unknown_year ? ["unknown"] : [])]
    : [], [reportView, yearsCurrent]);
  const visibleTotal = activeReportView?.total;
  const reportPageCount = Math.max(1, activeReportView?.page_count ?? 1);
  const currentReportPage = Math.max(0, (activeReportView?.page ?? 1) - 1);
  const groups = useMemo(() => groupReportsByFiscalYear(activeReportView?.items ?? []), [activeReportView]);
  const scopeName = managementScope === "city" ? "市级部门" : directory.byId.get(regionId)?.name ?? "区级部门";
  const selectedName = organizationId === "unassigned" ? "待确认归属（当前账号授权范围）" : currentOrganization && !isReportRegion(currentOrganization) ? currentOrganization.name : scopeName;
  const selectOrganization = (id: string, onlySelf = false) => {
    if (!id && managementScope === "city") id = organizations.find((item) => item.level === "city")?.id ?? "";
    setOrganizationId(id);
    setIncludeDescendants(!onlySelf);
    setYear(""); setKind("all"); setKeyword(""); setReviewStatus("all"); setReportPage(0);
    setOrgKeyword(""); setFavoritesOnly(false); setDirectoryPage(0);
    if (id === "unassigned") return;
    const option = directory.byId.get(id);
    if (option) setManagementScope(reportManagementScope(directory, id));
    setRegionId(reportDirectoryRegion(directory, id));
    const parentId = option?.parent_id && directory.byId.has(option.parent_id) ? option.parent_id : "";
    setDirectoryId(option && (isReportRegion(option) || (directory.children.get(id)?.length ?? 0) > 0) ? id : parentId);
  };
  const switchManagementScope = (scope: ReportManagementScope) => {
    setManagementScope(scope);
    const city = organizations.find((item) => item.level === "city");
    const rootId = scope === "city" ? city?.id ?? "" : "";
    setRegionId(rootId); setDirectoryId(rootId); setOrganizationId(rootId);
    setIncludeDescendants(true); setDirectoryPage(0); setReportPage(0);
    setOrgKeyword(""); setFavoritesOnly(false); setYear(""); setKind("all"); setKeyword(""); setReviewStatus("all");
  };
  const toggleFavorite = (id: string) => {
    const next = favorites.has(id) ? favoriteIds.filter((value) => value !== id) : [...favoriteIds, id].slice(-200);
    setFavoriteIds(next);
    if (favoriteStorageKey.current) {
      try { window.localStorage.setItem(favoriteStorageKey.current, JSON.stringify(next)); } catch { /* 保留本次访问中的收藏。 */ }
    }
  };
  const filterClass = "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50";
  const directoryTitle = orgKeyword.trim() ? "搜索结果" : favoritesOnly ? "收藏目录" : directoryOrganization && !isReportRegion(directoryOrganization) ? "本级与下属单位" : "部门与单位目录";

  return (
    <div className="mx-auto max-w-[1800px] p-6" data-testid="gbc-department-reports-page">
      <SectionTitle title="部门报告" desc="按市级、区级管理范围查找部门与单位，按财政年度查看预算和决算。" />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <p className="w-full min-w-0 text-xs text-slate-500 xl:w-auto xl:flex-1">每份材料一行；文件版本与分析任务分别记录。复核结果对应具体文件版本和分析代际。</p>
        <Link href={"/history" as Route} className={filterClass}>任务历史（兼容入口）</Link>
        <Link href={"/upload" as Route} className={filterClass}>上传新报告</Link>
        <button type="button" onClick={polling.refreshNow} disabled={polling.isManualRefreshing} className={filterClass} data-testid="gbc-reports-refresh">{polling.isManualRefreshing ? "刷新中…" : "刷新"}</button>
      </div>
      {error ? <p role="alert" className="mt-4 rounded-md bg-danger-50 p-3 text-sm text-danger-700">{error}{loaded ? " 当前保留上次成功加载的结果。" : ""}</p> : null}
      {!loaded && !error ? <p className="mt-4 text-sm text-slate-500">正在加载部门报告…</p> : null}
      <section className="mt-5" aria-label="部门与单位分级目录" data-testid="gbc-reports-directory">
        <div className="mb-4 flex border-b border-border" role="group" aria-label="报告管理范围">
          {([["city", "市级部门"], ["district", "区级部门"]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => switchManagementScope(value)} aria-pressed={managementScope === value} className={`border-b-2 px-5 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${managementScope === value ? "border-primary-600 text-primary-700" : "border-transparent text-slate-500 hover:text-primary-700"}`} data-testid={`gbc-reports-management-${value}`}>{label}</button>)}
        </div>
        <div className="mb-4 rounded-md bg-primary-50 px-4 py-3 text-sm text-primary-700" data-testid="gbc-reports-management-summary">
          {choosingDistrict && organizationId !== "unassigned" ? "请先选择区，再查看该区所属部门和材料。" : organizationId === "unassigned" ? "待确认归属的材料单独展示，范围为当前账号已有授权。" : `当前范围：${scopeName} · 仅包含本范围的部门及下属单位`}
        </div>
        {choosingDistrict && organizationId !== "unassigned" ? <div data-testid="gbc-reports-district-picker">
          <h2 className="mb-1 text-sm font-semibold text-slate-800">选择管理区</h2>
          <p className="mb-4 text-xs text-slate-500">选择区 → 选择部门 → 查看单位与材料。未配置组织目录的区暂不可进入。</p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {districtCards.map(({ name, organization }) => <button key={organization?.id ?? name} type="button" disabled={!organization} onClick={() => organization && selectOrganization(organization.id)} className="flex min-w-0 items-center justify-between gap-2 rounded-card border border-border bg-white px-4 py-4 text-left text-sm font-medium text-slate-800 hover:border-primary-300 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:bg-slate-50 disabled:text-slate-400 disabled:hover:border-border disabled:hover:bg-slate-50" data-testid={`gbc-reports-district-${organization?.id ?? name}`}><span className="break-words">{name}</span>{organization ? <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" /> : <span className="text-xs font-normal">未配置</span>}</button>)}
          </div>
          {loaded && districtCards.length === 0 ? <p className="mt-4 text-sm text-slate-500">当前账号没有可访问的区级组织目录。</p> : null}
        </div> : <>
        <div className="grid gap-3 sm:grid-cols-[220px_minmax(0,1fr)]">
          <select aria-label="选择报告所属地区" value={regionId} onChange={(event) => selectOrganization(event.target.value)} className={`${filterClass} w-full`} data-testid="gbc-reports-region">
            <option value="">{managementScope === "district" ? "请选择区" : "全部市级部门"}</option>
            {directory.regions.filter((item) => managementScope === "city" ? item.level === "city" : item.level === "district").map((item) => <option key={item.id} value={item.id}>{managementScope === "city" ? `${item.name} · 市级部门` : item.name}</option>)}
          </select>
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <input type="search" aria-label="搜索部门或单位" placeholder={`在${scopeName}范围内搜索部门或下属单位`} value={orgKeyword} onChange={(event) => { setOrgKeyword(event.target.value); setDirectoryPage(0); }} className={`${filterClass} w-full pl-9`} data-testid="gbc-reports-org-search" />
          </div>
        </div>
        {managementScope === "district" ? <button type="button" onClick={() => switchManagementScope("district")} className="mt-3 text-sm text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-back-to-districts">返回选区</button> : null}
        <nav className="mt-4 flex flex-wrap items-center gap-1 text-sm text-slate-500" aria-label="部门目录路径" data-testid="gbc-reports-breadcrumb">
          <button type="button" onClick={() => switchManagementScope(managementScope)} className="rounded px-1 py-1 text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-breadcrumb-root">{managementScope === "city" ? "市级部门" : "选择区"}</button>
          {directoryPath.map((item) => <span key={item.id} className="inline-flex min-w-0 items-center gap-1"><ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /><button type="button" onClick={() => selectOrganization(item.id)} className="break-words rounded px-1 py-1 text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid={`gbc-reports-breadcrumb-${item.id}`}>{item.name}</button></span>)}
        </nav>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-white px-3 py-2">
          <div><h2 className="text-sm font-medium text-slate-800">目录展示方式</h2><p className="text-xs text-slate-500" aria-live="polite">当前：{directoryView === "grid" ? "紧凑卡片" : "双栏列表"}</p></div>
          <div className="flex gap-1" role="group" aria-label="目录展示方式">{([["grid", "紧凑卡片"], ["list", "双栏列表"]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => setDirectoryView(value)} aria-pressed={directoryView === value} className={`${filterClass} ${directoryView === value ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid={`gbc-reports-view-${value}`}>{label}</button>)}</div>
        </div>
        <div className="my-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-700" aria-live="polite">{directoryTitle} <span className="font-normal text-slate-500">· {directoryItems.length} 项</span></h2>
          <div className="flex gap-2" aria-label="目录展示范围">
            <button type="button" onClick={() => { setFavoritesOnly(false); setDirectoryPage(0); }} aria-pressed={!favoritesOnly} className={`${filterClass} ${!favoritesOnly ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid="gbc-reports-directory-all">全部</button>
            <button type="button" onClick={() => { setFavoritesOnly(true); setDirectoryPage(0); }} aria-pressed={favoritesOnly} className={`${filterClass} inline-flex items-center gap-1 ${favoritesOnly ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid="gbc-reports-directory-favorites"><Star className="h-3.5 w-3.5" aria-hidden="true" />收藏</button>
          </div>
        </div>
        <div className={directoryView === "grid" ? "grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" : "grid grid-cols-1 gap-x-4 rounded-card border border-border bg-white px-3 xl:grid-cols-2"} data-testid="gbc-reports-directory-grid" data-view={directoryView}>
          {directoryItems.slice(currentDirectoryPage * directoryPageSize, (currentDirectoryPage + 1) * directoryPageSize).map((item) => {
            const ownCard = item.id === directoryId && !isReportRegion(item) && !orgKeyword.trim() && !favoritesOnly;
            const stats = scopedDirectory.stats.get(item.id)!;
            const childCount = directory.children.get(item.id)?.length ?? 0;
            const selectedCard = organizationId === item.id && (!ownCard || !includeDescendants);
            return <button key={item.id} type="button" onClick={() => selectOrganization(item.id, ownCard)} aria-pressed={selectedCard} data-testid={`gbc-reports-org-${item.id}`}
              className={`flex min-w-0 items-start gap-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${directoryView === "grid" ? "rounded-card border px-3 py-2.5" : "border-b px-1 py-2.5 last:border-b-0"} ${selectedCard ? "border-primary-400 bg-primary-50" : "border-border bg-white hover:border-primary-300 hover:bg-primary-50"}`}>
              <span className="mt-0.5 shrink-0 text-primary-700">{isReportRegion(item) ? <FolderOpen className="h-4 w-4" aria-hidden="true" /> : <Building2 className="h-4 w-4" aria-hidden="true" />}</span>
              <span className={`min-w-0 flex-1 ${directoryView === "list" ? "flex flex-wrap items-center justify-between gap-x-3 gap-y-1" : ""}`}>
                <span className="block break-words text-sm font-medium text-slate-900">{ownCard ? `${item.name}（本级）` : item.name}{favorites.has(item.id) ? <Star className="ml-1 inline h-3.5 w-3.5 text-primary-700" aria-label="已收藏" /> : null}</span>
                <span className={`${directoryView === "grid" ? "mt-1" : ""} flex flex-wrap justify-between gap-x-2 text-xs text-slate-500`}><span>{ownCard ? "本级" : `${reportOrganizationLevelLabel(item)}${stats.unitCount ? ` · ${stats.unitCount} 个下属单位` : childCount ? ` · ${childCount} 个下级组织` : ""}`}</span><span>{countsCurrent ? ownCard ? stats.ownReportCount : stats.reportCount : "—"} 份材料</span></span>
                {orgKeyword.trim() || favoritesOnly ? <span className="mt-1 block break-words text-xs text-slate-500">{reportDirectoryPath(directory, item.id)}</span> : null}
              </span>
              <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
            </button>;
          })}
        </div>
        {loaded && directoryItems.length === 0 ? <p role="status" className="rounded-card border border-dashed border-border bg-white p-6 text-center text-sm text-slate-500">{orgKeyword.trim() ? "没有找到匹配的部门或单位，可以缩短关键词或切换到全部地区。" : favoritesOnly ? "当前地区还没有收藏，进入部门或单位后点击标题旁的星标即可收藏。" : "当前目录没有下级组织。历史上传材料仍可在下方报告列表查看。"}</p> : null}
        {directoryItems.length > 0 ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500" data-testid="gbc-reports-directory-pagination">
          <span>共 {directoryItems.length} 项 · 当前 {currentDirectoryPage * directoryPageSize + 1}–{Math.min((currentDirectoryPage + 1) * directoryPageSize, directoryItems.length)} 项 · 第 {currentDirectoryPage + 1} / {directoryPageCount} 页</span>
          <div className="flex flex-wrap items-center gap-2"><label>每页 <select aria-label="每页目录数量" value={directoryPageSize} onChange={(event) => { setDirectoryPageSize(Number(event.target.value)); setDirectoryPage(0); }} className={filterClass} data-testid="gbc-reports-directory-page-size">{DIRECTORY_PAGE_SIZES.map((size) => <option key={size} value={size}>{size} 项</option>)}</select></label><button type="button" aria-label="目录上一页" disabled={currentDirectoryPage === 0} onClick={() => setDirectoryPage(currentDirectoryPage - 1)} className={filterClass}><ChevronLeft className="h-4 w-4" aria-hidden="true" /></button><button type="button" aria-label="目录下一页" disabled={currentDirectoryPage + 1 >= directoryPageCount} onClick={() => setDirectoryPage(currentDirectoryPage + 1)} className={filterClass}><ChevronRight className="h-4 w-4" aria-hidden="true" /></button></div>
        </div> : null}
        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          <button type="button" onClick={() => selectOrganization(regionId)} disabled={!loaded} className="rounded text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50" data-testid="gbc-reports-org-all">当前管理范围全部部门与单位</button>
          <button type="button" onClick={() => selectOrganization("unassigned")} disabled={!loaded} aria-pressed={organizationId === "unassigned"} className="rounded text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50" data-testid="gbc-reports-org-unassigned">待确认归属 · 授权范围（{reportView?.unassigned_total ?? "—"}）</button>
        </div>
        </>}
      </section>
      {!choosingDistrict || organizationId === "unassigned" ? <section className="mt-6 min-w-0 border-t border-border pt-5" aria-label="当前部门与单位的报告" data-testid="gbc-reports-selected">
          {currentOrganization ? <p className="mb-2 break-words text-xs text-slate-500" data-testid="gbc-reports-selected-path">{reportDirectoryPath(directory, currentOrganization.id)}</p> : null}
          <div className="flex items-start justify-between gap-3">
            <h2 className="break-words text-base font-semibold text-slate-900">{selectedName} <span className="text-sm font-normal text-slate-500" aria-live="polite">· {visibleTotal ?? "—"} 条材料记录</span></h2>
            {currentOrganization ? <button type="button" onClick={() => toggleFavorite(currentOrganization.id)} aria-pressed={favorites.has(currentOrganization.id)} aria-label={`${favorites.has(currentOrganization.id) ? "取消收藏" : "收藏"}${currentOrganization.name}`} className={`${filterClass} shrink-0 ${favorites.has(currentOrganization.id) ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid="gbc-reports-favorite"><Star className={`h-4 w-4 ${favorites.has(currentOrganization.id) ? "fill-current" : ""}`} aria-hidden="true" /></button> : null}
          </div>
          {currentOrganization && !isReportRegion(currentOrganization) && (directory.children.get(currentOrganization.id)?.length ?? 0) > 0 ? <div className="mt-3 flex flex-wrap gap-2" aria-label="部门报告包含范围">
            {SCOPES.map(([include, label]) => <button key={String(include)} type="button" aria-pressed={includeDescendants === include} onClick={() => { setIncludeDescendants(include); setYear(""); setReportPage(0); }} className={`${filterClass} ${includeDescendants === include ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid={`gbc-reports-scope-${include ? "all" : "self"}`}>{label}</button>)}
          </div> : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select aria-label="按财政年度查看报告" value={year} onChange={(event) => { setYear(event.target.value); setReportPage(0); }} className={filterClass} data-testid="gbc-reports-year">
              <option value="">全部财政年度</option>
              {years.map((value) => <option key={value} value={value}>{value === "unknown" ? "年度待确认" : `${value} 年`}</option>)}
            </select>
            <select aria-label="按复核状态查看材料" value={reviewStatus} onChange={(event) => { setReviewStatus(event.target.value); setReportPage(0); }} className={filterClass}>
              <option value="all">全部复核状态</option><option value="pending">待复核</option><option value="in_progress">复核中</option><option value="completed">已复核</option><option value="invalidated">需重新复核</option><option value="waiting_analysis">等待分析后复核</option><option value="history_only">未归属记录</option>
            </select>
            <input type="search" aria-label="搜索报告" placeholder="搜索报告文件名或单位" value={keyword} onChange={(event) => { setKeyword(event.target.value); setReportPage(0); }} className={`${filterClass} min-w-0 flex-1`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2" aria-label="按预算或决算查看报告">
            {KINDS.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setReportPage(0); }} data-testid={`gbc-reports-kind-${value}`} className={`${filterClass} ${kind === value ? "border-primary-400 bg-primary-50 font-medium text-primary-700" : ""}`}>{label}</button>)}
          </div>
          {loaded && !reportsCurrent ? <p className="mt-4 text-sm text-slate-500" role="status">正在加载当前范围的材料…</p> : null}
          {loaded && reportsCurrent && groups.length === 0 ? <p className="mt-5 rounded-md border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">当前筛选下没有报告。可以切换年度或材料类型，未关联组织的文件在“待确认归属”中查看。</p> : null}
          {loaded && reportsCurrent ? <p className="mt-3 text-xs text-slate-500">本范围已登记材料 {activeReportView?.material_total ?? "—"} 份 · 未归属记录 {activeReportView?.legacy_total ?? "—"} 条。目录按材料计数，未归属记录单独保留。</p> : null}
          {groups.map(([fiscalYear, reports]) => (
            <section key={fiscalYear} className="mt-5" data-testid={`gbc-reports-year-${fiscalYear}`}>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">{fiscalYear === "unknown" ? "财政年度待确认" : `${fiscalYear} 年度`} · {reports.length} 条材料记录{reportPageCount > 1 ? "（本页）" : ""}</h3>
              <MaterialReportTable items={reports} />
            </section>
          ))}
          {reportPageCount > 1 ? <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500" data-testid="gbc-reports-pagination"><span>共 {visibleTotal} 条材料记录 · 第 {currentReportPage + 1} / {reportPageCount} 页</span><div className="flex gap-2"><button type="button" disabled={currentReportPage === 0} onClick={() => setReportPage(currentReportPage - 1)} className={filterClass}>报告上一页</button><button type="button" disabled={currentReportPage + 1 >= reportPageCount} onClick={() => setReportPage(currentReportPage + 1)} className={filterClass}>报告下一页</button></div></div> : null}
      </section> : null}
    </div>
  );
}
