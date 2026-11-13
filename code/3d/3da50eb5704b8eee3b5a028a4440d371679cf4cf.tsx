"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Building2, ChevronLeft, ChevronRight, FolderOpen, Search, Star } from "lucide-react";
import { SectionTitle } from "@/components/ui";
import { resolvePollingDecision } from "@/lib/jobPolling";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { flattenOrganizationTree, type OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import { filterDepartmentReports, groupReportsByFiscalYear, type ReportKindFilter } from "./departmentReportAdapters";
import { buildReportDirectoryIndex, DIRECTORY_PAGE_SIZE, isReportRegion, REPORT_PAGE_SIZE, reportDirectoryItems, reportDirectoryPath, reportDirectoryRegion, reportOrganizationLevelLabel } from "./departmentDirectory";

const KINDS: Array<[ReportKindFilter, string]> = [["all", "全部材料"], ["budget", "预算"], ["final", "决算"], ["unknown", "类型待确认"]];
const SCOPES: Array<[boolean, string]> = [[true, "部门及下属单位"], [false, "仅部门本级"]];

/** 历史报告走上传任务的授权接口，未入材料槽位的文件也能按组织与财政年度查到。 */
export function DepartmentReportsPage() {
  const [jobs, setJobs] = useState<JobSummaryRecord[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationFilterOption[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [year, setYear] = useState("");
  const [kind, setKind] = useState<ReportKindFilter>("all");
  const [keyword, setKeyword] = useState("");
  const [orgKeyword, setOrgKeyword] = useState("");
  const [directoryId, setDirectoryId] = useState("");
  const [regionId, setRegionId] = useState("");
  const [directoryPage, setDirectoryPage] = useState(0);
  const [reportPage, setReportPage] = useState(0);
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
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
      const [jobResponse, orgResponse] = await Promise.all([
        fetch("/api/jobs", { cache: "no-store" }),
        fetch("/api/organizations?stats=none", { cache: "no-store" }),
      ]);
      if (!jobResponse.ok || !orgResponse.ok) throw new Error(`加载失败（HTTP ${!jobResponse.ok ? jobResponse.status : orgResponse.status}）`);
      const jobPayload = await jobResponse.json();
      const orgPayload = await orgResponse.json();
      const next = Array.isArray(jobPayload) ? jobPayload : jobPayload.items ?? [];
      jobsRef.current = next;
      setJobs(next);
      const nextOrganizations = flattenOrganizationTree(orgPayload.tree);
      setOrganizations(nextOrganizations);
      const knownIds = new Set(nextOrganizations.map((item) => item.id));
      setDirectoryId((current) => knownIds.has(current) ? current : "");
      setRegionId((current) => knownIds.has(current) ? current : "");
      setOrganizationId((current) => current === "unassigned" || knownIds.has(current) ? current : "");
      setLoaded(true);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "报告加载失败");
      throw reason;
    }
  }, []);
  const polling = useJobPolling({ fetcher: load, decide: useCallback(() => resolvePollingDecision(jobsRef.current), []) });
  const directory = useMemo(() => buildReportDirectoryIndex(organizations, jobs), [organizations, jobs]);
  useEffect(() => {
    if (!loaded || directoryInitialized.current) return;
    directoryInitialized.current = true;
    const roots = directory.children.get(null) ?? [];
    // 只有一个市级/区级入口时直接进入，省去重复点击“上海市”等唯一目录。
    if (roots.length === 1 && isReportRegion(roots[0])) {
      setDirectoryId(roots[0].id);
      setRegionId(roots[0].id);
      setOrganizationId(roots[0].id);
    }
  }, [directory, loaded]);
  const favorites = useMemo(() => new Set(favoriteIds), [favoriteIds]);
  const directoryItems = useMemo(() => reportDirectoryItems(directory, { parentId: directoryId, regionId, keyword: orgKeyword, favoritesOnly, favoriteIds: favorites }), [directory, directoryId, regionId, orgKeyword, favoritesOnly, favorites]);
  const directoryPageCount = Math.max(1, Math.ceil(directoryItems.length / DIRECTORY_PAGE_SIZE));
  const currentDirectoryPage = Math.min(directoryPage, directoryPageCount - 1);
  const currentOrganization = directory.byId.get(organizationId);
  const directoryOrganization = directory.byId.get(directoryId);
  const directoryPath = directory.paths.get(directoryId) ?? [];
  const organizationJobs = useMemo(() => filterDepartmentReports(jobs, organizations, { organizationId, year: "", kind: "all", keyword: "", includeDescendants }), [jobs, organizations, organizationId, includeDescendants]);
  const years = useMemo(() => groupReportsByFiscalYear(organizationJobs).map(([value]) => value), [organizationJobs]);
  const visible = useMemo(() => filterDepartmentReports(jobs, organizations, { organizationId, year, kind, keyword, includeDescendants }), [jobs, organizations, organizationId, year, kind, keyword, includeDescendants]);
  const reportPageCount = Math.max(1, Math.ceil(visible.length / REPORT_PAGE_SIZE));
  const currentReportPage = Math.min(reportPage, reportPageCount - 1);
  const groups = useMemo(() => groupReportsByFiscalYear(visible.slice(currentReportPage * REPORT_PAGE_SIZE, (currentReportPage + 1) * REPORT_PAGE_SIZE)), [visible, currentReportPage]);
  const selectedName = organizationId === "unassigned" ? "待确认归属" : currentOrganization?.name ?? "全部部门与单位";
  const selectOrganization = (id: string, onlySelf = false) => {
    setOrganizationId(id);
    setIncludeDescendants(!onlySelf);
    setYear(""); setKind("all"); setKeyword(""); setReportPage(0);
    setOrgKeyword(""); setFavoritesOnly(false); setDirectoryPage(0);
    if (id === "unassigned") return;
    const option = directory.byId.get(id);
    setRegionId(reportDirectoryRegion(directory, id));
    const parentId = option?.parent_id && directory.byId.has(option.parent_id) ? option.parent_id : "";
    setDirectoryId(option && (isReportRegion(option) || (directory.children.get(id)?.length ?? 0) > 0) ? id : parentId);
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
      <SectionTitle title="部门报告" desc="按地区、部门与下属单位逐级查找，按财政年度查看预算和决算。" />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <p className="flex-1 text-xs text-slate-500">财政年度来自报告内容，与上传时间、公开日期分别记录。未识别到的年度和类型单独展示。</p>
        <Link href={"/upload" as Route} className={filterClass}>上传新报告</Link>
        <button type="button" onClick={polling.refreshNow} disabled={polling.isManualRefreshing} className={filterClass} data-testid="gbc-reports-refresh">{polling.isManualRefreshing ? "刷新中…" : "刷新"}</button>
      </div>
      {error ? <p role="alert" className="mt-4 rounded-md bg-danger-50 p-3 text-sm text-danger-700">{error}；请刷新重试。{loaded ? "当前保留上次成功加载的结果。" : ""}</p> : null}
      {!loaded && !error ? <p className="mt-4 text-sm text-slate-500">正在加载部门报告…</p> : null}
      <section className="mt-5" aria-label="部门与单位分级目录" data-testid="gbc-reports-directory">
        <div className="grid gap-3 sm:grid-cols-[220px_minmax(0,1fr)]">
          <select aria-label="选择报告所属地区" value={regionId} onChange={(event) => selectOrganization(event.target.value)} className={`${filterClass} w-full`} data-testid="gbc-reports-region">
            <option value="">全部地区</option>
            {directory.regions.map((item) => <option key={item.id} value={item.id}>{reportDirectoryPath(directory, item.id)}</option>)}
          </select>
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" aria-hidden="true" />
            <input type="search" aria-label="搜索部门或单位" placeholder={regionId ? "在当前地区搜索部门或下属单位" : "搜索所有地区的部门或下属单位"} value={orgKeyword} onChange={(event) => { setOrgKeyword(event.target.value); setDirectoryPage(0); }} className={`${filterClass} w-full pl-9`} data-testid="gbc-reports-org-search" />
          </div>
        </div>
        <nav className="mt-4 flex flex-wrap items-center gap-1 text-sm text-slate-500" aria-label="部门目录路径" data-testid="gbc-reports-breadcrumb">
          <button type="button" onClick={() => selectOrganization("")} className="rounded px-1 py-1 text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid="gbc-reports-breadcrumb-root">全部地区</button>
          {directoryPath.map((item) => <span key={item.id} className="inline-flex min-w-0 items-center gap-1"><ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /><button type="button" onClick={() => selectOrganization(item.id)} className="break-words rounded px-1 py-1 text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" data-testid={`gbc-reports-breadcrumb-${item.id}`}>{item.name}</button></span>)}
        </nav>
        <div className="my-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-700" aria-live="polite">{directoryTitle} <span className="font-normal text-slate-500">· {directoryItems.length} 项</span></h2>
          <div className="flex gap-2" aria-label="目录展示范围">
            <button type="button" onClick={() => { setFavoritesOnly(false); setDirectoryPage(0); }} aria-pressed={!favoritesOnly} className={`${filterClass} ${!favoritesOnly ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid="gbc-reports-directory-all">全部</button>
            <button type="button" onClick={() => { setFavoritesOnly(true); setDirectoryPage(0); }} aria-pressed={favoritesOnly} className={`${filterClass} inline-flex items-center gap-1 ${favoritesOnly ? "border-primary-400 bg-primary-50 text-primary-700" : ""}`} data-testid="gbc-reports-directory-favorites"><Star className="h-3.5 w-3.5" aria-hidden="true" />收藏</button>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="gbc-reports-directory-grid">
          {directoryItems.slice(currentDirectoryPage * DIRECTORY_PAGE_SIZE, (currentDirectoryPage + 1) * DIRECTORY_PAGE_SIZE).map((item) => {
            const ownCard = item.id === directoryId && !isReportRegion(item) && !orgKeyword.trim() && !favoritesOnly;
            const stats = directory.stats.get(item.id)!;
            const childCount = directory.children.get(item.id)?.length ?? 0;
            const selectedCard = organizationId === item.id && (!ownCard || !includeDescendants);
            return <button key={item.id} type="button" onClick={() => selectOrganization(item.id, ownCard)} aria-pressed={selectedCard} data-testid={`gbc-reports-org-${item.id}`}
              className={`flex min-w-0 items-start gap-3 rounded-card border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${selectedCard ? "border-primary-400 bg-primary-50" : "border-border bg-white hover:border-primary-300 hover:bg-primary-50"}`}>
              <span className="rounded-md bg-primary-100 p-2 text-primary-700">{isReportRegion(item) ? <FolderOpen className="h-4 w-4" aria-hidden="true" /> : <Building2 className="h-4 w-4" aria-hidden="true" />}</span>
              <span className="min-w-0 flex-1">
                <span className="block break-words text-sm font-medium text-slate-900">{ownCard ? `${item.name}（本级）` : item.name}{favorites.has(item.id) ? <Star className="ml-1 inline h-3.5 w-3.5 text-primary-700" aria-label="已收藏" /> : null}</span>
                <span className="mt-1 block break-words text-xs text-slate-500">{ownCard ? "仅查看本级报告" : `${reportOrganizationLevelLabel(item)}${stats.unitCount ? ` · ${stats.unitCount} 个下属单位` : childCount ? ` · ${childCount} 个下级组织` : ""}`}</span>
                {orgKeyword.trim() || favoritesOnly ? <span className="mt-1 block break-words text-xs text-slate-500">{reportDirectoryPath(directory, item.id)}</span> : null}
                <span className="mt-1 block text-xs text-slate-500">{ownCard ? stats.ownReportCount : stats.reportCount} 份报告</span>
              </span>
              <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
            </button>;
          })}
        </div>
        {loaded && directoryItems.length === 0 ? <p role="status" className="rounded-card border border-dashed border-border bg-white p-6 text-center text-sm text-slate-500">{orgKeyword.trim() ? "没有找到匹配的部门或单位，可以缩短关键词或切换到全部地区。" : favoritesOnly ? "当前地区还没有收藏，进入部门或单位后点击标题旁的星标即可收藏。" : "当前目录没有下级组织。历史上传材料仍可在下方报告列表查看。"}</p> : null}
        {directoryItems.length > 0 ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500" data-testid="gbc-reports-directory-pagination">
          <span>共 {directoryItems.length} 项 · 第 {currentDirectoryPage + 1} / {directoryPageCount} 页</span>
          <div className="flex gap-2"><button type="button" aria-label="目录上一页" disabled={currentDirectoryPage === 0} onClick={() => setDirectoryPage(currentDirectoryPage - 1)} className={filterClass}><ChevronLeft className="h-4 w-4" aria-hidden="true" /></button><button type="button" aria-label="目录下一页" disabled={currentDirectoryPage + 1 >= directoryPageCount} onClick={() => setDirectoryPage(currentDirectoryPage + 1)} className={filterClass}><ChevronRight className="h-4 w-4" aria-hidden="true" /></button></div>
        </div> : null}
        <div className="mt-4 flex flex-wrap gap-3 text-sm">
          <button type="button" onClick={() => selectOrganization("")} disabled={!loaded} aria-pressed={!organizationId} className="rounded text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50" data-testid="gbc-reports-org-all">全部部门与单位</button>
          <button type="button" onClick={() => selectOrganization("unassigned")} disabled={!loaded} aria-pressed={organizationId === "unassigned"} className="rounded text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:opacity-50" data-testid="gbc-reports-org-unassigned">待确认归属</button>
        </div>
      </section>
      <section className="mt-6 min-w-0 border-t border-border pt-5" aria-label="当前部门与单位的报告" data-testid="gbc-reports-selected">
          {currentOrganization ? <p className="mb-2 break-words text-xs text-slate-500" data-testid="gbc-reports-selected-path">{reportDirectoryPath(directory, currentOrganization.id)}</p> : null}
          <div className="flex items-start justify-between gap-3">
            <h2 className="break-words text-base font-semibold text-slate-900">{selectedName} <span className="text-sm font-normal text-slate-500" aria-live="polite">· {visible.length} 份报告</span></h2>
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
            <input type="search" aria-label="搜索报告" placeholder="搜索报告文件名或单位" value={keyword} onChange={(event) => { setKeyword(event.target.value); setReportPage(0); }} className={`${filterClass} min-w-0 flex-1`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2" aria-label="按预算或决算查看报告">
            {KINDS.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setReportPage(0); }} data-testid={`gbc-reports-kind-${value}`} className={`${filterClass} ${kind === value ? "border-primary-400 bg-primary-50 font-medium text-primary-700" : ""}`}>{label}</button>)}
          </div>
          {loaded && groups.length === 0 ? <p className="mt-5 rounded-md border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">当前筛选下没有报告。可以切换年度或材料类型，未关联组织的文件在“待确认归属”中查看。</p> : null}
          {groups.map(([fiscalYear, reports]) => (
            <section key={fiscalYear} className="mt-5" data-testid={`gbc-reports-year-${fiscalYear}`}>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">{fiscalYear === "unknown" ? "财政年度待确认" : `${fiscalYear} 年度`} · {reports.length} 份{reportPageCount > 1 ? "（本页）" : ""}</h3>
              <WorkbenchQueueTable jobs={reports} collapsibleChecks renderRowActions={(job) => (
                <a href={`/api/files/${encodeURIComponent(job.job_id)}/source`} className="whitespace-nowrap rounded-md border border-border px-2.5 py-1.5 text-xs text-slate-600">下载原件</a>
              )} />
            </section>
          ))}
          {visible.length > REPORT_PAGE_SIZE ? <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-500" data-testid="gbc-reports-pagination"><span>共 {visible.length} 份报告 · 第 {currentReportPage + 1} / {reportPageCount} 页</span><div className="flex gap-2"><button type="button" disabled={currentReportPage === 0} onClick={() => setReportPage(currentReportPage - 1)} className={filterClass}>报告上一页</button><button type="button" disabled={currentReportPage + 1 >= reportPageCount} onClick={() => setReportPage(currentReportPage + 1)} className={filterClass}>报告下一页</button></div></div> : null}
      </section>
    </div>
  );
}
