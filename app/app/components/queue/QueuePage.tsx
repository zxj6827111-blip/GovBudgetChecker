/**
 * QueuePage：Task 8.1 处理队列全量页——工作台队列面板的全量版。
 *
 * 复用：
 * - WorkbenchQueueTable（阶段进度/质量徽章/耗时/页数/问题数列）；
 * - 筛选、排序、分页由 /api/jobs?view=page 在服务端完成（api/job_list_query.py，
 *   口径同 queuePageAdapters.filterQueueJobs）；后端未升级时回退到本地同口径计算；
 * - OrganizationFilterSelect（Task 4 的组织筛选器，跨屏共享组件）；
 * - 年份选项来自接口汇总（含"未识别到"特殊态）。
 *
 * 与侧边栏角标的口径一致性：分页视图带回全部可见任务的状态计数，本页"正在处理"
 * 的数字与侧栏角标都取自同一份 status_counts.analyzing，并通过 publishJobCounts
 * 同步给侧栏，两个数字必然一致。
 *
 * URL 上的 ?job=<id>（工作台队列行的跳转目标）作为初始关键词，
 * 让"从工作台点进某个任务"直接定位到那一行，而不是回到无筛选的全量列表。
 */
"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, SectionTitle } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { publishJobCounts } from "@/lib/jobListEvents";
import { fetchJobPage, type JobPageView } from "@/lib/jobListViews";

import { OrganizationFilterSelect } from "../workspace/OrganizationFilterSelect";
import { PollRefreshStatus } from "../workspace/PollRefreshStatus";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import type { WorkbenchStatusFilter } from "../workspace/workbenchAdapters";
import {
  QUEUE_SORT_LABELS,
  QUEUE_STAGE_FILTER_LABELS,
  buildQueuePageViewLocally,
  queueFiltersFromQuery,
  queuePageQuery,
  queueReturnPath,
  type QueuePageFilters,
  type QueueReportKindFilter,
  type QueueSortKey,
  type QueueStageFilter,
} from "./queuePageAdapters";
import { resolvePollingDecisionFromActivity } from "@/lib/jobPolling";
import {
  canRunQueueJob,
  requestQueueRun,
  runQueueBatch,
  type QueueBatchProgress,
  type QueueBatchResult,
} from "./queueBatchActions";

const PAGE_SIZE = 20;

const STATUS_FILTER_OPTIONS: Array<{ value: WorkbenchStatusFilter; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "review_required", label: "待人工复核" },
  { value: "pending_analysis", label: "待分析" },
  { value: "analyzing", label: "正在处理" },
  { value: "failed", label: "处理失败" },
  { value: "completed", label: "已完成" },
];

const KIND_FILTER_OPTIONS: Array<{ value: QueueReportKindFilter; label: string }> = [
  { value: "all", label: "全部类型" },
  { value: "budget", label: "部门预算" },
  { value: "final", label: "部门决算" },
  { value: "unknown", label: "待复核（未识别）" },
];

const SORT_OPTIONS: Array<{ value: QueueSortKey; label: string }> = (
  Object.keys(QUEUE_SORT_LABELS) as QueueSortKey[]
).map((key) => ({ value: key, label: QUEUE_SORT_LABELS[key] }));

const STAGE_OPTIONS: Array<{ value: QueueStageFilter; label: string }> = (
  Object.keys(QUEUE_STAGE_FILTER_LABELS) as QueueStageFilter[]
).map((key) => ({ value: key, label: QUEUE_STAGE_FILTER_LABELS[key] }));

export function QueuePage() {
  const searchParams = useSearchParams();
  const [view, setView] = useState<JobPageView | null>(null);
  const [filters, setFilters] = useState<QueuePageFilters>(() => queueFiltersFromQuery(searchParams));
  const [sortKey, setSortKey] = useState<QueueSortKey>(() => {
    const sort = searchParams.get("sort") ?? "updated_desc";
    return Object.hasOwn(QUEUE_SORT_LABELS, sort) ? sort as QueueSortKey : "updated_desc";
  });
  const [page, setPage] = useState(() => Math.max(1, Math.floor(Number(searchParams.get("page")) || 1)));
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [isBatchRunning, setIsBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState<QueueBatchProgress | null>(null);
  const [batchResult, setBatchResult] = useState<QueueBatchResult | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);
  const [busyJobIds, setBusyJobIds] = useState<Set<string>>(() => new Set());
  const batchRunningRef = useRef(false);
  const busyJobIdsRef = useRef<Set<string>>(new Set());

  /** 最新分页视图的旁路引用：轮询决策每次续排时实时读取，不受闭包过期影响。 */
  const viewRef = useRef<JobPageView | null>(null);
  viewRef.current = view;
  /** 当前筛选/排序/页码对应的查询；取数总是读最新值。 */
  const queryRef = useRef<{ filters: QueuePageFilters; sortKey: QueueSortKey; page: number }>({ filters, sortKey, page });
  const abortRef = useRef<AbortController | null>(null);

  /** 修复 1：任务状态轮询。只取当前一页加全量汇总，不再下载全部任务摘要。
   *  失败时抛错（由轮询层上报并继续重试），保持旧数据。 */
  const loadPage = useCallback(async () => {
    // 请求期间筛选又变了：旧请求已被中止或结果作废，按最新条件再读一次。
    for (;;) {
      const query = queryRef.current;
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const next = await fetchJobPage(
          queuePageQuery(query.filters, query.sortKey, query.page, PAGE_SIZE),
          (jobs) => buildQueuePageViewLocally(jobs, query.filters, query.sortKey, query.page, PAGE_SIZE),
          controller.signal,
        );
        if (query !== queryRef.current) continue;
        // 必须在 setView 之外同步更新旁路引用：轮询决策在 fetch 返回的同一微任务里
        // 立即执行（早于 React 重渲染）。
        viewRef.current = next;
        setView(next);
        publishJobCounts({ total: next.job_total, status_counts: next.status_counts, activity: next.activity });
        if (next.page !== query.page) {
          // 服务端把超出范围的页码收敛到了最后一页：本地页码跟上，且不再触发重复请求。
          queryRef.current = { ...query, page: next.page };
          setPage(next.page);
        }
        return;
      } catch (error) {
        if (controller.signal.aborted && query !== queryRef.current) continue;
        throw error;
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    }
  }, []);

  const polling = useJobPolling({
    fetcher: loadPage,
    decide: useCallback(() => resolvePollingDecisionFromActivity(viewRef.current?.activity), []),
  });
  const refreshJobs = polling.refreshNow;
  /** 提交运行后立即按当前条件重读：换一个新的查询对象，进行中的旧请求结果随之作废。 */
  const reloadNow = useCallback(() => {
    queryRef.current = { ...queryRef.current };
    abortRef.current?.abort();
    refreshJobs();
  }, [refreshJobs]);

  // 筛选、排序或翻页后重新取数；关键词输入稍作停顿再请求，避免每敲一个字打一次后端。
  const lastKeywordRef = useRef(filters.keyword);
  useEffect(() => {
    const current = queryRef.current;
    if (current.filters === filters && current.sortKey === sortKey && current.page === page) return;
    const delay = filters.keyword !== lastKeywordRef.current ? 300 : 0;
    lastKeywordRef.current = filters.keyword;
    const timer = window.setTimeout(() => {
      queryRef.current = { filters, sortKey, page };
      abortRef.current?.abort();
      refreshJobs();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [filters, sortKey, page, refreshJobs]);

  const availableYears = view?.years ?? [];
  const runnableJobs = useMemo(() => (view?.runnable ?? []) as JobSummaryRecord[], [view]);
  const selectedJobs = useMemo(() => runnableJobs.filter((job) => selectedIds.has(String(job.job_id))), [runnableJobs, selectedIds]);

  useEffect(() => {
    if (!view || isBatchRunning) return;
    // 选中项只能是当前筛选下仍可运行的任务（跨页保留，来自接口的 runnable 列表）。
    const eligible = new Set(runnableJobs.map((job) => String(job.job_id)));
    setSelectedIds((previous) => {
      const next = new Set(Array.from(previous).filter((id) => eligible.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [view, runnableJobs, isBatchRunning]);

  // 筛选变化后页码重置到第一页（否则可能停留在超出范围的空页）
  const updateFilters = useCallback((patch: Partial<QueuePageFilters>) => {
    if (batchRunningRef.current) return;
    setFilters((prev) => ({ ...prev, ...patch }));
    setPage(1);
    setSelectedIds(new Set());
  }, []);

  const handleRunJob = useCallback(
    async (jobId: string) => {
      if (batchRunningRef.current || busyJobIdsRef.current.has(jobId)) return;
      const job = viewRef.current?.items.find((item) => String(item.job_id) === jobId);
      if (!job || !canRunQueueJob(job)) return;
      busyJobIdsRef.current.add(jobId);
      setBusyJobIds(new Set(busyJobIdsRef.current));
      setRunNotice(null);
      try {
        await requestQueueRun(job);
        setRunNotice(`${job.filename ?? jobId} 已提交分析，请在队列中查看进度。`);
      } catch (error) {
        setRunNotice(`${job.filename ?? jobId}：${error instanceof Error ? error.message : "提交未成功。"}`);
      } finally {
        busyJobIdsRef.current.delete(jobId);
        setBusyJobIds(new Set(busyJobIdsRef.current));
        reloadNow();
      }
    },
    [reloadNow],
  );

  const handleBatchRun = useCallback(async () => {
    if (batchRunningRef.current || busyJobIdsRef.current.size > 0) return;
    const targets = selectedJobs;
    if (targets.length === 0) return;
    batchRunningRef.current = true;
    setIsBatchRunning(true);
    setBatchResult(null);
    setRunNotice(null);
    try {
      const result = await runQueueBatch(targets, requestQueueRun, setBatchProgress);
      setBatchResult(result);
      const submitted = new Set(result.submittedIds);
      setSelectedIds((previous) => new Set(Array.from(previous).filter((id) => !submitted.has(id))));
    } finally {
      batchRunningRef.current = false;
      setIsBatchRunning(false);
      // 使用轮询控制器的刷新入口，全部终态时已经停止的轮询也会重新启动。
      reloadNow();
    }
  }, [selectedJobs, reloadNow]);

  return (
    <div className="p-8" data-testid="gbc-queue-page">
      <SectionTitle
        title="处理队列"
        desc={`查看机器分析的运行记录、阶段进度与异常。当前正在处理 ${view ? view.status_counts.analyzing : "—"} 次分析；侧边栏角标表示正在处理数，列表保留历史记录。`}
      />

      <fieldset disabled={isBatchRunning} className="mt-6 mb-3 min-w-0 border-0 p-0">
      <legend className="sr-only">处理队列筛选</legend>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={filters.keyword}
          onChange={(event) => updateFilters({ keyword: event.target.value })}
          placeholder="搜索文件名、组织或任务 ID"
          data-testid="gbc-queue-search"
          className="min-w-[220px] flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        <select
          value={filters.status}
          onChange={(event) => updateFilters({ status: event.target.value as WorkbenchStatusFilter })}
          data-testid="gbc-queue-status-filter"
          aria-label="按状态筛选"
          className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          {STATUS_FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          value={filters.year}
          onChange={(event) => updateFilters({ year: event.target.value })}
          data-testid="gbc-queue-year-filter"
          aria-label="按年份筛选"
          className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <option value="">全部年份</option>
          {availableYears.map((year) => (
            <option key={year} value={String(year)}>
              {year}
            </option>
          ))}
          <option value="unresolved">未识别到</option>
        </select>
        <select
          value={filters.reportKind}
          onChange={(event) =>
            updateFilters({ reportKind: event.target.value as QueueReportKindFilter })
          }
          data-testid="gbc-queue-kind-filter"
          aria-label="按文档类型筛选"
          className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          {KIND_FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          value={filters.stage}
          onChange={(event) => updateFilters({ stage: event.target.value as QueueStageFilter })}
          data-testid="gbc-queue-stage-filter"
          aria-label="按阶段筛选"
          className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          {STAGE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <OrganizationFilterSelect
          value={filters.organizationId}
          onChange={(organizationId) => updateFilters({ organizationId })}
          data-testid="gbc-queue-org-filter"
        />
        <select
          value={sortKey}
          onChange={(event) => {
            setSortKey(event.target.value as QueueSortKey);
            setPage(1);
          }}
          data-testid="gbc-queue-sort"
          aria-label="排序方式"
          className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      </fieldset>

      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border bg-white px-3 py-2" data-testid="gbc-queue-batch-toolbar">
        <span className="mr-auto text-sm text-slate-600" data-testid="gbc-queue-selected-count">已选 {selectedJobs.length} 个任务</span>
        <Button
          variant="ghost"
          size="sm"
          disabled={isBatchRunning || busyJobIds.size > 0 || runnableJobs.length === 0}
          onClick={() => setSelectedIds(new Set(runnableJobs.map((job) => String(job.job_id))))}
          data-testid="gbc-queue-select-filtered"
        >全选筛选结果{view === null ? "" : `（${runnableJobs.length}）`}</Button>
        <Button variant="ghost" size="sm" disabled={isBatchRunning || selectedJobs.length === 0} onClick={() => setSelectedIds(new Set())} data-testid="gbc-queue-clear-selection">清空选择</Button>
        <Button
          size="sm"
          onClick={() => void handleBatchRun()}
          disabled={isBatchRunning || busyJobIds.size > 0 || selectedJobs.length === 0}
          data-testid="gbc-queue-batch-run"
        >{isBatchRunning ? `提交中 ${batchProgress?.completed ?? 0}/${batchProgress?.total ?? selectedJobs.length}` : `批量重新运行（${selectedJobs.length}）`}</Button>
        <span className="w-full text-xs text-slate-500">勾选可跨页保留；全选覆盖当前筛选的全部页。运行中的任务自动跳过，待分析任务会启动首次分析。</span>
      </div>
      {isBatchRunning && batchProgress ? (
        <div className="mb-3 text-sm text-slate-600" role="status" data-testid="gbc-queue-batch-progress">正在提交 {batchProgress.completed}/{batchProgress.total}，已提交 {batchProgress.submitted}，未提交 {batchProgress.failed}。</div>
      ) : null}
      {batchResult ? (
        <div className="mb-3 rounded-md border border-border bg-white px-3 py-2 text-sm" data-testid="gbc-queue-batch-result">
          <p className="text-slate-700" role="status">批量提交结束：已提交 {batchResult.submittedIds.length} 个，未提交 {batchResult.failures.length} 个。分析进度请查看队列。</p>
          {batchResult.failures.length > 0 ? (
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs text-danger-700" aria-label="未提交任务及原因">
              {batchResult.failures.map((failure) => <li key={failure.jobId}>{failure.filename}：{failure.message}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}
      {runNotice ? <p role="status" className="mb-3 text-sm text-slate-600" data-testid="gbc-queue-run-notice">{runNotice}</p> : null}

      <div className="mb-3 flex items-center justify-between text-sm text-slate-500">
        <span data-testid="gbc-queue-result-count">
          {/* 首次读取完成前不知道任务数，不能显示"共 0 个任务"。 */}
          {view === null
            ? polling.lastErrorMessage ? "任务数暂不可用" : "正在统计任务…"
            : <>
                共 {view.total} 个任务
                {view.total !== view.job_total ? `（总 ${view.job_total} 个，当前筛选条件下）` : ""}
              </>}
        </span>
        <PollRefreshStatus
          lastSyncedAt={polling.lastSyncedAt}
          lastErrorMessage={polling.lastErrorMessage}
          isRefreshing={polling.isManualRefreshing}
          onRefresh={polling.refreshNow}
          testIdPrefix="gbc-queue-refresh"
        />
        {view && view.page_count > 1 ? (
          <span data-testid="gbc-queue-pagination-info">
            第 {view.page} / {view.page_count} 页
          </span>
        ) : null}
      </div>

      {view === null ? (
        <div
          className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-queue-loading"
        >
          {polling.lastErrorMessage ? "处理队列暂时无法读取，请点击上方刷新重试。" : "正在加载处理队列…"}
        </div>
      ) : (
        <WorkbenchQueueTable
          reviewReturnTo={queueReturnPath(filters, sortKey, view.page)}
          jobs={view.items}
          onReanalyze={(jobId) => void handleRunJob(jobId)}
          onStartAnalysis={(jobId) => void handleRunJob(jobId)}
          actionsDisabled={isBatchRunning}
          busyJobIds={busyJobIds}
          selection={{
            selectedIds,
            canSelect: canRunQueueJob,
            disabled: isBatchRunning || busyJobIds.size > 0,
            onToggleJob: (jobId) => setSelectedIds((previous) => {
              const next = new Set(previous);
              if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
              return next;
            }),
            onTogglePage: (jobIds, selected) => setSelectedIds((previous) => {
              const next = new Set(previous);
              jobIds.forEach((id) => { if (selected) next.add(id); else next.delete(id); });
              return next;
            }),
          }}
        />
      )}

      {view && view.page_count > 1 ? (
        <div className="mt-4 flex items-center justify-end gap-2" data-testid="gbc-queue-pagination">
          <Button
            variant="secondary"
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            disabled={view.page <= 1}
            data-testid="gbc-queue-prev-page"
          >
            上一页
          </Button>
          <Button
            variant="secondary"
            onClick={() => setPage((prev) => Math.min(view.page_count, prev + 1))}
            disabled={view.page >= view.page_count}
            data-testid="gbc-queue-next-page"
          >
            下一页
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export default QueuePage;
