"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ArrowUpRight, Download, History, Info } from "lucide-react";
import { Button } from "@/components/ui";
import { publishJobCounts } from "@/lib/jobListEvents";
import { fetchJobOverview, type JobOverviewQuery, type JobOverviewView } from "@/lib/jobListViews";
import { resolvePollingDecisionFromActivity } from "@/lib/jobPolling";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { readCoveragePayload, type MaterialCoverageSummary } from "../materials/materialLedgerAdapters";
import { WorkbenchActivityPanel } from "./WorkbenchActivityPanel";
import { WorkbenchAlertsPanel } from "./WorkbenchAlertsPanel";
import { WorkbenchQueueTable } from "./WorkbenchQueueTable";
import { OrganizationFilterSelect, flattenOrganizationTree, type OrganizationFilterOption } from "./OrganizationFilterSelect";
import { PollRefreshStatus } from "./PollRefreshStatus";
import { buildJobOverviewLocally, cockpitCoverageFromOverview } from "./jobOverviewAdapters";
import { useJobPageLoader } from "./useJobPageLoader";
import { useJobPolling } from "./useJobPolling";
import { ANALYZE_START_REQUEST_BODY } from "./uploadCenterAdapters";
import { deriveQualityAlerts, type WorkbenchStatusFilter } from "./workbenchAdapters";
import { buildCockpitProgress, buildCockpitTaskPageLocally, buildDepartmentCoverage, cockpitPercent,
  cockpitOverviewCsv, cockpitTaskQuery, cockpitTrendFromDaily, sameCockpitTaskKey, validCockpitLedger,
  type CockpitState, type CockpitTaskKey } from "./workbenchCockpitAdapters";
import { CockpitDonut, CockpitTrendChart } from "./WorkbenchCockpitCharts";
import { WorkbenchCockpitDrawer } from "./WorkbenchCockpitDrawer";

// 沿用旧工作台的参考阈值；实际质量门禁仍以任务结果为准。
const PAGE_COVERAGE_REFERENCE = 0.8;
// 趋势一次取 30 天，切换"近 7 日"只截取，不再请求。
const OVERVIEW_QUERY: JobOverviewQuery = { coverageMin: PAGE_COVERAGE_REFERENCE, trendDays: 30 };
const toTaskQuery = (key: CockpitTaskKey) => cockpitTaskQuery(key, PAGE_COVERAGE_REFERENCE);
const buildTasksLocally = (jobs: JobSummaryRecord[], key: CockpitTaskKey) => buildCockpitTaskPageLocally(jobs, key, PAGE_COVERAGE_REFERENCE);
type Detail = CockpitState | "all" | "tasks" | "coverage" | "low-coverage" | "quality" | "definitions" | "activity";
const TITLES: Record<Detail, string> = { all: "全部材料", tasks: "分析任务明细", completed: "已完成人工复核",
  review: "待人工复核", processing: "正在处理", failed: "处理失败", other: "其他材料状态", coverage: "部门覆盖情况",
  "low-coverage": "页面覆盖率不足的任务", quality: "质量概览", definitions: "统计口径", activity: "最近活动" };
const FILTERS: Array<[WorkbenchStatusFilter, string]> = [["all", "全部"], ["review_required", "待人工复核"],
  ["pending_analysis", "待分析"], ["analyzing", "正在处理"], ["failed", "处理失败"], ["completed", "分析已结束"]];
const LEDGER_STATES: Record<string, Array<[string, string]>> = {
  all: [["completed", "已完成复核"], ["review_required", "待开始复核"], ["reviewing", "复核中"], ["processing", "正在处理"], ["failed", "处理失败"], ["uploaded", "待分析"], ["mapping_required", "身份待确认"], ["missing", "逾期未上传"], ["not_due", "尚未确认到期"], ["not_applicable", "不适用"]],
  completed: [["completed", "已完成复核"]], review: [["review_required", "待开始复核"], ["reviewing", "复核中"]],
  processing: [["processing", "正在处理"]], failed: [["failed", "处理失败"]],
  other: [["uploaded", "待分析"], ["mapping_required", "身份待确认"], ["missing", "逾期未上传"], ["not_due", "尚未确认到期"], ["not_applicable", "不适用"]],
};

function Panel({ title, action, children, testId }: { title: string; action?: ReactNode; children: ReactNode; testId: string }) {
  return <section data-testid={testId} className="min-w-0 rounded-card border border-border bg-white p-4 shadow-ui">
    <div className="mb-2 flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-slate-900">{title}</h2>{action}</div>{children}
  </section>;
}
function MetricTile({ label, value, unit, tone = "text-slate-900", onClick, testId }: {
  label: string; value: ReactNode; unit?: string; tone?: string; onClick: () => void; testId: string;
}) {
  return <button type="button" onClick={onClick} data-testid={testId} className="min-w-0 rounded-card border border-border bg-white px-4 py-2.5 text-left shadow-ui transition-colors hover:border-primary-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">
    <div className="text-xs text-slate-500">{label}</div><div className={`mt-2 text-[28px] font-semibold leading-tight tabular-nums ${tone}`}>{value ?? "—"}{unit ? <span className="ml-1 text-xs font-normal text-slate-400">{unit}</span> : null}</div>
  </button>;
}

/**
 * 已批准的全局总览：首页只聚合，任务表和活动按需打开。
 * 任务侧统计取自 /api/jobs?view=overview 的汇总；抽屉里的任务明细按页读取
 * （/api/jobs?view=page），都不再下载全部任务摘要。
 */
export function WorkbenchCockpitPage() {
  const [overview, setOverview] = useState<JobOverviewView | null>(null);
  const [ledger, setLedger] = useState<MaterialCoverageSummary | null>(null);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [organizations, setOrganizations] = useState<OrganizationFilterOption[] | null>(null);
  const [orgError, setOrgError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<{ jobs?: { total?: number }; quality?: { unknown_report_kind?: { ratio?: number | null } } } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [days, setDays] = useState<7 | 30>(7);
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState<WorkbenchStatusFilter>("all");
  const [yearFilter, setYearFilter] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [taskSession, setTaskSession] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);
  /** 最新汇总的旁路引用：轮询决策在 fetch 返回的同一微任务里读取，早于重渲染。 */
  const overviewRef = useRef<JobOverviewView | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const detailTrigger = useRef<HTMLElement | null>(null);
  const closeDetail = useCallback(() => {
    // 先解除背景禁用，抽屉卸载时才能把键盘焦点归还给打开它的按钮。
    pageRef.current?.removeAttribute("inert");
    setDetail(null);
  }, []);
  useEffect(() => { pageRef.current?.toggleAttribute("inert", detail !== null); }, [detail]);

  const loadOverview = useCallback(async () => {
    const next = await fetchJobOverview(OVERVIEW_QUERY, (jobs) => buildJobOverviewLocally(jobs, OVERVIEW_QUERY));
    overviewRef.current = next; setOverview(next);
    publishJobCounts({ total: next.total, status_counts: next.status_counts, activity: next.activity });
  }, []);
  const tasks = useJobPageLoader<CockpitTaskKey>({
    initialKey: { session: 0, lowCoverage: false, keyword: "", status: "all", year: "", organizationId: "", page: 1 },
    toQuery: toTaskQuery, buildLocally: buildTasksLocally, onPageClamped: setTaskPage,
  });
  /** 抽屉里的任务表是否打开：打开时随轮询一起刷新。 */
  const taskTableOpenRef = useRef(false);
  const loadTasks = tasks.load;
  // 明细读取失败只在抽屉里提示，不让整页统计显示为失败。
  const refreshOpenTasks = useCallback(() => taskTableOpenRef.current ? loadTasks().catch(() => undefined) : undefined, [loadTasks]);
  const loadLedger = useCallback(async () => {
    try {
      const response = await fetch("/api/materials/coverage", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = readCoveragePayload(await response.json());
      if (!payload || !validCockpitLedger(payload.data.summary)) throw new Error("统计响应不完整");
      setLedger(payload.data.summary); setLedgerError(null);
    } catch (error) {
      // 保留旧值并标记失效；请求失败不能被显示成零材料或全部完成。
      setLedgerError(error instanceof Error ? error.message : "请求失败");
    }
  }, []);
  const loadOrganizations = useCallback(async () => {
    try {
      const response = await fetch("/api/organizations?stats=none", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json() as { tree?: Parameters<typeof flattenOrganizationTree>[0] };
      if (!Array.isArray(payload.tree)) throw new Error("组织响应不完整");
      setOrganizations(flattenOrganizationTree(payload.tree)); setOrgError(null);
    } catch (error) { setOrgError(error instanceof Error ? error.message : "请求失败"); }
  }, []);
  const loadMetrics = useCallback(async () => {
    try {
      const response = await fetch("/api/metrics", { cache: "no-store" });
      // 管理员补充指标无权限时静默降级，普通审核员的总览仍然可用。
      if (response.ok) setMetrics(await response.json());
    } catch { /* 不阻断任务与台账统计。 */ }
  }, []);
  const polling = useJobPolling({
    fetcher: useCallback(async () => {
      await Promise.all([loadOverview(), loadLedger(), loadOrganizations(), loadMetrics(), refreshOpenTasks()]);
    }, [loadOverview, loadLedger, loadOrganizations, loadMetrics, refreshOpenTasks]),
    decide: useCallback(() => resolvePollingDecisionFromActivity(overviewRef.current?.activity), []),
  });
  const refreshNow = polling.refreshNow;
  const progress = useMemo(() => buildCockpitProgress(overview, ledger), [overview, ledger]);
  const departmentCoverage = useMemo(() => buildDepartmentCoverage(overview, organizations), [overview, organizations]);
  const pageCoverage = useMemo(() => cockpitCoverageFromOverview(overview?.page_coverage), [overview]);
  const lowCoverageCount = overview?.low_coverage_count ?? null;
  const trend = useMemo(() => cockpitTrendFromDaily(overview?.trend ?? null, days), [overview, days]);
  // 告警计数与"页面覆盖率不足"明细同一口径（接口的 low_coverage_count）。
  const qualityAlerts = useMemo(() => overview === null ? null : deriveQualityAlerts({ lowCoverageCount: overview.low_coverage_count,
    unknownReportKindRatio: metrics?.quality?.unknown_report_kind?.ratio, metricsJobsTotal: metrics?.jobs?.total,
    pageCoverageMinRatio: PAGE_COVERAGE_REFERENCE, unknownReportKindMaxRatio: 0.05, minSampleSizeForRatioAlerts: 20 }), [overview, metrics]);

  const openDetail = (next: Detail) => {
    // 在禁用背景之前记录入口，抽屉内部切换明细时仍返回最初入口。
    if (detail === null) detailTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDetail(next); setKeyword(""); setYearFilter(""); setOrganizationId(""); setActionError(null);
    setStatusFilter(next === "review" ? "review_required" : next === "processing" ? "analyzing" : next === "failed" ? "failed" : "all");
    setTaskPage(1); setTaskSession((value) => value + 1);
  };
  const { keyRef: taskKeyRef, setKey: setTaskKey } = tasks;
  const runOne = useCallback(async (jobId: string, initial: boolean) => {
    setActionError(null);
    try {
      const url = initial ? `/api/analyze/${encodeURIComponent(jobId)}` : `/api/jobs/${encodeURIComponent(jobId)}/reanalyze`;
      const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ANALYZE_START_REQUEST_BODY) });
      if (!response.ok) throw new Error(`提交失败（HTTP ${response.status}），请在处理队列查看原因。`);
      // 换一个新的查询对象：进行中的旧明细请求结果作废，按当前条件重读。
      setTaskKey({ ...taskKeyRef.current });
      await refreshNow();
    } catch (error) { setActionError(error instanceof Error ? error.message : "提交失败，请稍后重试。"); }
  }, [refreshNow, setTaskKey, taskKeyRef]);
  const exportOverview = () => {
    const url = URL.createObjectURL(new Blob([cockpitOverviewCsv(progress, departmentCoverage, pageCoverage, trend, days)], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `工作台概览_${new Date().toLocaleDateString("sv-SE")}.csv`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const showTaskTable = detail === "tasks" || detail === "low-coverage" ||
    (progress?.source !== "ledger" && detail !== "completed" && ["all", "review", "processing", "failed", "other"].includes(detail ?? ""));
  taskTableOpenRef.current = showTaskTable;

  // 打开明细、改筛选或翻页后按页取数；关键词输入稍作停顿再请求。
  const lastKeywordRef = useRef(keyword);
  useEffect(() => {
    if (!showTaskTable) return;
    const key: CockpitTaskKey = { session: taskSession, lowCoverage: detail === "low-coverage", keyword,
      status: statusFilter, year: yearFilter, organizationId, page: taskPage };
    if (sameCockpitTaskKey(taskKeyRef.current, key)) return;
    const delay = keyword !== lastKeywordRef.current ? 300 : 0;
    lastKeywordRef.current = keyword;
    const timer = window.setTimeout(() => { setTaskKey(key); loadTasks().catch(() => undefined); }, delay);
    return () => window.clearTimeout(timer);
  }, [showTaskTable, taskSession, detail, keyword, statusFilter, yearFilter, organizationId, taskPage, taskKeyRef, setTaskKey, loadTasks]);
  // 只显示本次打开抽屉后的结果；本次内改筛选时先保留上一页结果，直到新结果返回。
  const taskView = tasks.viewKey?.session === taskSession ? tasks.view : null;

  return <>
    <div ref={pageRef} className="mx-auto max-w-[1600px] px-6 py-3" data-testid="gbc-workbench-page">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight text-slate-900">审校全局概览</h1><p className="mt-1 text-sm text-slate-500">看整体进展，识别当前需要关注的事项。</p></div>
        <div className="flex items-center gap-3"><Link href={"/supervision" as Route} className="rounded-md border border-border bg-white px-3 py-2 text-sm text-primary-700">查看主管端</Link><span className="rounded-md bg-surface-100 px-2 py-1 text-xs text-slate-500">全局总览</span><Button variant="secondary" onClick={exportOverview} disabled={overview === null} data-testid="gbc-cockpit-export"><Download className="h-4 w-4" />导出概览</Button></div>
      </div>
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" data-testid="gbc-cockpit-metrics">
        <MetricTile label={progress?.source === "jobs" ? "材料任务总量" : "材料总量"} value={progress?.total} unit={progress?.source === "jobs" ? "个" : "条"} onClick={() => openDetail("all")} testId="gbc-cockpit-kpi-total" />
        <MetricTile label="待人工复核" value={progress?.review} unit="条" tone="text-warning-700" onClick={() => openDetail("review")} testId="gbc-workbench-kpi-review-required" />
        <MetricTile label="处理失败" value={progress?.failed} unit="条" tone="text-danger-600" onClick={() => openDetail("failed")} testId="gbc-workbench-kpi-failed" />
        <MetricTile label="覆盖部门" value={departmentCoverage ? <>{departmentCoverage.covered}<span className="text-lg font-normal text-slate-400"> / {departmentCoverage.total}</span></> : null} unit="个" tone="text-primary-700" onClick={() => openDetail("coverage")} testId="gbc-cockpit-kpi-departments" />
        <MetricTile label="页面覆盖率" value={cockpitPercent(pageCoverage?.averageRatio)} tone="text-primary-700" onClick={() => openDetail("quality")} testId="gbc-workbench-kpi-coverage" />
      </div>
      <div className="mb-3 grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(270px,1fr)]">
        <Panel title="审校总体进度" testId="gbc-cockpit-progress" action={<button type="button" onClick={() => openDetail("definitions")} className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-primary-700">统计口径<Info className="h-3.5 w-3.5" /></button>}>
          <div className="flex items-center gap-5"><CockpitDonut progress={progress} /><div className="grid min-w-0 flex-1 grid-cols-2 gap-x-6 gap-y-3">
            {([["completed", "已完成复核", "bg-success-600"], ["review", "待人工复核", "bg-warning-600"], ["processing", "正在处理", "bg-info-500"], ["failed", "处理失败", "bg-danger-600"]] as const).map(([state, label, dot]) =>
              <button key={state} type="button" onClick={() => openDetail(state)} className="text-left" data-testid={`gbc-cockpit-state-${state}`}><span className="flex items-center gap-1.5 text-xs text-slate-500"><span className={`h-1.5 w-1.5 shrink-0 rounded-sm ${dot}`} />{label}</span><strong className="mt-1 block text-xl font-semibold tabular-nums text-slate-800">{progress?.[state] ?? "—"}</strong></button>)}
          </div></div>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400"><span>{progress?.source === "ledger" ? "已建档材料台账；人工复核完成单独统计" : "当前为任务统计，人工复核完成情况暂无可用数据"}</span>{progress && progress.other > 0 ? <button type="button" onClick={() => openDetail("other")} className="hover:text-primary-700">其他状态 {progress.other} 条</button> : null}</div>
        </Panel>
        <Panel title="风险与待办" testId="gbc-cockpit-risks" action={<span className="text-xs text-slate-400">点击查看明细</span>}>
          {[{ id: "review" as const, label: "待完成人工复核", count: progress?.review, tone: "text-warning-700", unit: "条" }, { id: "failed" as const, label: "任务处理失败", count: progress?.failed, tone: "text-danger-600", unit: "条" }, { id: "low-coverage" as const, label: "页面覆盖率不足", count: lowCoverageCount, tone: "text-warning-700", unit: "个任务" }].map((risk) =>
            <button key={risk.id} type="button" onClick={() => openDetail(risk.id)} data-testid={`gbc-cockpit-risk-${risk.id}`} className="flex w-full items-center justify-between gap-2 border-b border-border py-2 text-left text-sm last:border-b-0 hover:text-primary-700"><span>{risk.label}</span><span className="flex items-center gap-1.5"><strong className={`text-lg font-semibold tabular-nums ${risk.tone}`}>{risk.count ?? "—"}</strong><span className="text-xs text-slate-400">{risk.unit}</span><ArrowRight className="h-3.5 w-3.5 text-slate-400" /></span></button>)}
        </Panel>
      </div>
      <div className="mb-3 grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(270px,1fr)]">
        <Panel title="审校处理趋势" testId="gbc-cockpit-trend" action={<div className="flex rounded-md bg-surface-100 p-0.5">{([7, 30] as const).map((value) => <button key={value} type="button" aria-pressed={days === value} onClick={() => setDays(value)} data-testid={`gbc-cockpit-period-${value}`} className={`rounded px-2 py-1 text-xs ${days === value ? "bg-white text-primary-700 shadow-sm" : "text-slate-400"}`}>近{value}日</button>)}</div>}>
          <div className="mb-1 flex flex-wrap gap-4 text-xs text-slate-400"><span className="flex items-center gap-1.5"><i className="h-0.5 w-3 bg-info-500" />新增任务</span><span className="flex items-center gap-1.5"><i className="h-0.5 w-3 bg-primary-600" />结束分析</span></div><CockpitTrendChart trend={trend} />
          {trend && (trend.missingUploadTimes || trend.missingAnalysisTimes) ? <p className="mt-1 text-xs text-slate-400">{trend.missingUploadTimes} 个任务缺上传时间，{trend.missingAnalysisTimes} 个已结束任务缺结束时间，未纳入对应趋势。</p> : null}
        </Panel>
        <Panel title="部门覆盖情况" testId="gbc-cockpit-department-coverage" action={<span className="text-xs text-slate-400">当前可见范围</span>}>
          <div className="mb-3 text-[30px] font-semibold tabular-nums text-primary-700">{cockpitPercent(departmentCoverage?.ratio)}</div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-100" role="progressbar" aria-label="有已关联材料的部门占比" aria-valuemin={0} aria-valuemax={100} aria-valuenow={departmentCoverage?.ratio != null ? departmentCoverage.ratio * 100 : undefined}><div className="h-full rounded-full bg-primary-600" style={{ width: `${(departmentCoverage?.ratio ?? 0) * 100}%` }} /></div>
          <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-slate-500"><span>有已关联材料 {departmentCoverage?.covered ?? "—"} 个</span><span>暂无关联材料 {departmentCoverage ? departmentCoverage.total - departmentCoverage.covered : "—"} 个</span></div>
          <p className="mt-3 rounded-md bg-surface-50 p-2 text-xs text-slate-500">关联覆盖不代表公开完整性。{departmentCoverage?.unlinkedJobs ? `另有 ${departmentCoverage.unlinkedJobs} 个任务未关联到部门。` : ""}</p>
          {orgError ? <p className="mt-2 text-xs text-warning-700">组织信息获取失败，覆盖统计可能未更新。</p> : null}<button type="button" onClick={() => openDetail("coverage")} className="mt-3 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-primary-700">查看部门覆盖明细<ArrowRight className="h-3.5 w-3.5" /></button>
        </Panel>
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400"><PollRefreshStatus lastSyncedAt={polling.lastSyncedAt} lastErrorMessage={polling.lastErrorMessage} isRefreshing={polling.isManualRefreshing} onRefresh={polling.refreshNow} testIdPrefix="gbc-workbench-refresh" /><div className="flex items-center gap-4"><button type="button" onClick={() => openDetail("activity")} data-testid="gbc-cockpit-activity" className="inline-flex items-center gap-1 hover:text-primary-700"><History className="h-3.5 w-3.5" />最近活动</button><Link href="/upload" className="hover:text-primary-700">上传材料</Link><button type="button" onClick={() => openDetail("all")} data-testid="gbc-cockpit-all" className="inline-flex items-center gap-1 hover:text-primary-700">查看全部材料<ArrowUpRight className="h-3.5 w-3.5" /></button></div></footer>
      {ledgerError ? <p className="mt-2 text-xs text-warning-700" data-testid="gbc-cockpit-ledger-unavailable">台账统计暂不可用（{ledgerError}）。{ledger ? "保留上次已获取的台账统计，请刷新确认。" : "当前先展示可见任务；人工复核完成情况保持未知。"}</p> : null}
    </div>
    {detail ? <WorkbenchCockpitDrawer title={TITLES[detail]} onClose={closeDetail} returnFocusTo={detailTrigger.current}>
      {detail === "definitions" ? <div className="space-y-5 text-sm text-slate-600"><div><h3 className="font-semibold text-slate-900">材料与任务</h3><p className="mt-1">台账统计按已建档材料计算；台账不可用或尚未建档时显示当前可见任务，不推断历史版本为独立应收材料。</p></div><div><h3 className="font-semibold text-slate-900">人工复核与分析结束</h3><p className="mt-1">已完成复核只取材料台账的 completed 状态。已复核占比 = 已完成复核材料 / 已建档材料总量；没有台账数据时保持未知。趋势统计上传和分析结束事件，不表示人工复核完成。</p></div><div><h3 className="font-semibold text-slate-900">部门覆盖</h3><p className="mt-1">按可见组织树中的部门和任务组织关联统计，下属单位归入其父部门。不根据单位名称猜测，不把暂无关联材料直接判为违规缺失。</p></div><div><h3 className="font-semibold text-slate-900">页面覆盖率</h3><p className="mt-1">仅聚合覆盖率在 0～100% 范围内的真实任务样本。低覆盖任务采用 80% 参考阈值，实际审校门禁以任务结果为准。</p></div></div> : null}
      {detail === "activity" ? <WorkbenchActivityPanel /> : null}
      {detail === "quality" ? <><p className="mb-4 text-sm text-slate-500">页面覆盖率 {cockpitPercent(pageCoverage?.averageRatio)}，有效样本 {pageCoverage?.sampleSize ?? 0} 个；{pageCoverage?.excludedCount ?? "—"} 个缺失或无效字段未纳入聚合。</p><WorkbenchAlertsPanel alerts={qualityAlerts} onAlertClick={(alert) => openDetail(alert.id === "page_coverage_low" ? "low-coverage" : "tasks")} /></> : null}
      {detail === "coverage" ? <>
        <p className="mb-4 text-sm text-slate-500">仅统计已关联材料；另有 {departmentCoverage?.unlinkedJobs ?? "—"} 个任务未关联到部门。暂无关联材料不等同于未公开。</p>
        {departmentCoverage ? <div className="space-y-2">{departmentCoverage.departments.length ? departmentCoverage.departments.map((org) =>
          <div key={org.id} data-testid={`gbc-cockpit-department-${org.id}`} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3 text-sm">
            <span>{org.name}</span><div className="flex items-center gap-4"><span className={`text-xs ${org.covered ? "text-primary-700" : "text-slate-400"}`}>{org.covered ? "已关联材料" : "暂无关联材料"}</span><Link href={`/reports/${encodeURIComponent(org.id)}` as Route} className="text-xs text-primary-700">查看部门报告 →</Link></div>
          </div>) : <p className="rounded-md bg-surface-50 p-4 text-sm text-slate-500">当前可见范围暂无部门。</p>}</div> : <p className="text-sm text-slate-500">暂无可用部门覆盖数据。</p>}
      </> : null}
      {LEDGER_STATES[detail] && progress?.source === "ledger" && ledger ? <><p className="mb-4 text-sm text-slate-500">按材料台账统计，人工复核与分析任务状态分别展示。</p><div className="grid gap-3 sm:grid-cols-2">{LEDGER_STATES[detail].map(([key, label]) => <Link key={key} href={`/materials?status=${key}` as Route} className="flex items-center justify-between rounded-md border border-border p-4 text-sm hover:border-primary-300"><span>{label}</span><strong className="text-xl tabular-nums text-primary-700">{ledger.status_counts[key as keyof typeof ledger.status_counts]}</strong></Link>)}</div><div className="mt-5 flex flex-wrap gap-3"><Link href="/materials" className="text-sm text-primary-700">打开材料台账 →</Link><button type="button" onClick={() => openDetail("tasks")} className="text-sm text-primary-700">查看分析任务明细 →</button></div></> : null}
      {detail === "completed" && progress?.source !== "ledger" ? <p className="rounded-md bg-surface-50 p-4 text-sm text-slate-500">暂无可用人工复核统计。分析结束不能作为人工复核完成的依据，请在材料台账或审核工作台查看实际复核状态。</p> : null}
      {showTaskTable ? <><div className="mb-4 flex flex-wrap items-center gap-2"><input value={keyword} onChange={(event) => { setKeyword(event.target.value); setTaskPage(1); }} placeholder="搜索文件名、组织或任务 ID" data-testid="gbc-workbench-search" aria-label="搜索分析任务" className="min-w-0 flex-1 rounded-md border border-border px-3 py-2 text-sm" /><select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as WorkbenchStatusFilter); setTaskPage(1); }} data-testid="gbc-workbench-status-filter" aria-label="按任务状态筛选" className="rounded-md border border-border px-3 py-2 text-sm">{FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={yearFilter} onChange={(event) => { setYearFilter(event.target.value); setTaskPage(1); }} data-testid="gbc-workbench-year-filter" aria-label="按材料年度筛选" className="rounded-md border border-border px-3 py-2 text-sm"><option value="">全部年份</option>{(tasks.view?.years ?? []).map((year) => <option key={year}>{year}</option>)}<option value="unresolved">未识别到</option></select><OrganizationFilterSelect value={organizationId} onChange={(value) => { setOrganizationId(value); setTaskPage(1); }} /></div><p className="mb-3 text-xs text-slate-400">{taskView ? `当前筛选 ${taskView.total} 个分析任务。` : "正在统计分析任务…"}批量操作可在<Link href="/queue" className="text-primary-700">处理队列</Link>中执行。</p>{actionError ? <p role="alert" className="mb-3 rounded-md bg-danger-50 p-3 text-sm text-danger-700">{actionError}</p> : null}{tasks.error ? <p role="alert" data-testid="gbc-cockpit-tasks-error" className="mb-3 rounded-md bg-warning-50 p-3 text-sm text-warning-800">{tasks.error}{taskView ? " 当前保留上次成功加载的结果。" : ""}</p> : null}{taskView === null ? (tasks.error ? null : <p className="text-sm text-slate-400" data-testid="gbc-cockpit-tasks-loading">正在加载任务…</p>) : <>
        <WorkbenchQueueTable jobs={taskView.items} collapsibleChecks onReanalyze={(id) => void runOne(id, false)} onStartAnalysis={(id) => void runOne(id, true)} />
        {taskView.page_count > 1 ? <div className="mt-3 flex items-center justify-end gap-2 text-xs text-slate-500" data-testid="gbc-cockpit-tasks-pagination"><span>第 {taskView.page} / {taskView.page_count} 页</span><button type="button" onClick={() => setTaskPage((value) => Math.max(1, value - 1))} disabled={taskView.page <= 1} data-testid="gbc-cockpit-tasks-prev" className="rounded-md border border-border px-2.5 py-1 hover:bg-surface-50 disabled:cursor-not-allowed disabled:opacity-50">上一页</button><button type="button" onClick={() => setTaskPage((value) => Math.min(taskView.page_count, value + 1))} disabled={taskView.page >= taskView.page_count} data-testid="gbc-cockpit-tasks-next" className="rounded-md border border-border px-2.5 py-1 hover:bg-surface-50 disabled:cursor-not-allowed disabled:opacity-50">下一页</button></div> : null}
      </>}</> : null}
    </WorkbenchCockpitDrawer> : null}
  </>;
}
