/**
 * QueuePage：Task 8.1 处理队列全量页——工作台队列面板的全量版。
 *
 * 复用：
 * - WorkbenchQueueTable（阶段进度/质量徽章/耗时/页数/问题数列）；
 * - workbenchAdapters.filterWorkbenchQueue 的四维筛选（keyword/status/year/org）
 *   由 queuePageAdapters.filterQueueJobs 内部调用，再叠加文档类型与阶段两维；
 * - OrganizationFilterSelect（Task 4 的组织筛选器，跨屏共享组件）；
 * - collectAvailableYears 年份选项（含"未识别到"特殊态）。
 *
 * 与侧边栏角标的口径一致性：侧边栏"处理队列"角标 = 全量 jobs 中
 * status 归一为 analyzing 的任务数（navBadges.computeNavBadgeCounts）；
 * 本页"正在处理"筛选的结果数来自同一份 /api/jobs 数据与同一归一函数
 * （queuePageAdapters.countAnalyzingJobs），两个数字必然一致。
 *
 * URL 上的 ?job=<id>（工作台队列行的跳转目标）作为初始关键词，
 * 让"从工作台点进某个任务"直接定位到那一行，而不是回到无筛选的全量列表。
 */
"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button, SectionTitle } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { publishJobList } from "@/lib/jobListEvents";
import { jobListRequest } from "@/lib/listRequests";

import { OrganizationFilterSelect } from "../workspace/OrganizationFilterSelect";
import { PollRefreshStatus } from "../workspace/PollRefreshStatus";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import { collectAvailableYears, type WorkbenchStatusFilter } from "../workspace/workbenchAdapters";
import {
  QUEUE_SORT_LABELS,
  QUEUE_STAGE_FILTER_LABELS,
  countAnalyzingJobs,
  filterQueueJobs,
  paginateJobs,
  sortQueueJobs,
  queueFiltersFromQuery,
  queueReturnPath,
  type QueuePageFilters,
  type QueueReportKindFilter,
  type QueueSortKey,
  type QueueStageFilter,
} from "./queuePageAdapters";
import { resolvePollingDecision } from "@/lib/jobPolling";
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
  const [jobs, setJobs] = useState<JobSummaryRecord[] | null>(null);
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

  /** 最新 jobs 的旁路引用：轮询决策每次续排时实时读取，不受闭包过期影响。 */
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
  jobsRef.current = jobs;

  /** 修复 1：任务状态轮询（此前本页只有挂载时一次 fetch，任务完成后界面不刷新）。
   *  失败时抛错（由轮询层上报并继续重试），保持旧数据。 */
  const loadJobs = useCallback(async () => {
    const next = await jobListRequest.load();
    // 必须在 setJobs 之外同步更新旁路引用：轮询决策在 fetch 返回的同一微任务里
    // 立即执行（早于 React 重渲染），走渲染期赋值会让终态判定晚一个周期、
    // 多打一次后端。
    jobsRef.current = next;
    setJobs(next);
    publishJobList(next);
  }, []);

  const polling = useJobPolling({
    fetcher: loadJobs,
    decide: useCallback(() => resolvePollingDecision(jobsRef.current), []),
  });
  const refreshJobs = polling.refreshNow;

  // 工作台队列行的跳转目标（/queue?job=<id>）：作为初始关键词定位该任务。
  // 只在任务数据首次加载前应用一次，之后用户自己的筛选不被覆盖。
  const initialJobParam = searchParams.get("job");
  useEffect(() => {
    if (initialJobParam && jobs === null) {
      setFilters((prev) => (prev.keyword ? prev : { ...prev, keyword: initialJobParam }));
    }
  }, [initialJobParam, jobs]);

  const availableYears = useMemo(() => collectAvailableYears(jobs), [jobs]);
  const analyzingCount = useMemo(() => countAnalyzingJobs(jobs), [jobs]);

  const filteredJobs = useMemo(() => filterQueueJobs(jobs, filters), [jobs, filters]);
  const sortedJobs = useMemo(() => sortQueueJobs(filteredJobs, sortKey), [filteredJobs, sortKey]);
  const pagination = useMemo(() => paginateJobs(sortedJobs, page, PAGE_SIZE), [sortedJobs, page]);
  const eligibleFilteredJobs = useMemo(() => filteredJobs.filter(canRunQueueJob), [filteredJobs]);
  const selectedJobs = useMemo(() => (jobs ?? []).filter((job) => selectedIds.has(String(job.job_id))), [jobs, selectedIds]);

  useEffect(() => {
    if (!jobs || isBatchRunning) return;
    const eligible = new Set(jobs.filter(canRunQueueJob).map((job) => String(job.job_id)));
    setSelectedIds((previous) => {
      const next = new Set(Array.from(previous).filter((id) => eligible.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [jobs, isBatchRunning]);

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
      const job = jobsRef.current?.find((item) => String(item.job_id) === jobId);
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
        jobListRequest.reset();
        refreshJobs();
      }
    },
    [refreshJobs],
  );

  const handleBatchRun = useCallback(async () => {
    if (batchRunningRef.current || busyJobIdsRef.current.size > 0) return;
    const targets = (jobsRef.current ?? []).filter((job) => selectedIds.has(String(job.job_id)));
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
      jobListRequest.reset();
      refreshJobs();
    }
  }, [selectedIds, refreshJobs]);

  return (
    <div className="p-8" data-testid="gbc-queue-page">
      <SectionTitle
        title="处理队列"
        desc={`查看机器分析的运行记录、阶段进度与异常。当前正在处理 ${analyzingCount} 次分析；侧边栏角标表示正在处理数，列表保留历史记录。`}
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
          disabled={isBatchRunning || busyJobIds.size > 0 || eligibleFilteredJobs.length === 0}
          onClick={() => setSelectedIds(new Set(eligibleFilteredJobs.map((job) => String(job.job_id))))}
          data-testid="gbc-queue-select-filtered"
        >全选筛选结果（{eligibleFilteredJobs.length}）</Button>
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
          共 {pagination.total} 个任务
          {pagination.total !== (jobs?.length ?? 0) ? `（总 ${jobs?.length ?? 0} 个，当前筛选条件下）` : ""}
        </span>
        <PollRefreshStatus
          lastSyncedAt={polling.lastSyncedAt}
          lastErrorMessage={polling.lastErrorMessage}
          isRefreshing={polling.isManualRefreshing}
          onRefresh={polling.refreshNow}
          testIdPrefix="gbc-queue-refresh"
        />
        {pagination.pageCount > 1 ? (
          <span data-testid="gbc-queue-pagination-info">
            第 {pagination.page} / {pagination.pageCount} 页
          </span>
        ) : null}
      </div>

      {jobs === null ? (
        <div
          className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-queue-loading"
        >
          {polling.lastErrorMessage ? "处理队列暂时无法读取，请点击上方刷新重试。" : "正在加载处理队列…"}
        </div>
      ) : (
        <WorkbenchQueueTable
          reviewReturnTo={queueReturnPath(filters, sortKey, page)}
          jobs={pagination.items}
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

      {pagination.pageCount > 1 ? (
        <div className="mt-4 flex items-center justify-end gap-2" data-testid="gbc-queue-pagination">
          <Button
            variant="secondary"
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            disabled={pagination.page <= 1}
            data-testid="gbc-queue-prev-page"
          >
            上一页
          </Button>
          <Button
            variant="secondary"
            onClick={() => setPage((prev) => Math.min(pagination.pageCount, prev + 1))}
            disabled={pagination.page >= pagination.pageCount}
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
