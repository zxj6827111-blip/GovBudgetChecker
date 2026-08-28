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

import { OrganizationFilterSelect } from "../workspace/OrganizationFilterSelect";
import { PollRefreshStatus } from "../workspace/PollRefreshStatus";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import { collectAvailableYears, type WorkbenchStatusFilter } from "../workspace/workbenchAdapters";
import {
  DEFAULT_QUEUE_FILTERS,
  QUEUE_SORT_LABELS,
  QUEUE_STAGE_FILTER_LABELS,
  countAnalyzingJobs,
  filterQueueJobs,
  paginateJobs,
  sortQueueJobs,
  type QueuePageFilters,
  type QueueReportKindFilter,
  type QueueSortKey,
  type QueueStageFilter,
} from "./queuePageAdapters";
import { resolvePollingDecision } from "@/lib/jobPolling";

const PAGE_SIZE = 20;

const STATUS_FILTER_OPTIONS: Array<{ value: WorkbenchStatusFilter; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "review_required", label: "待人工复核" },
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
  const [filters, setFilters] = useState<QueuePageFilters>(DEFAULT_QUEUE_FILTERS);
  const [sortKey, setSortKey] = useState<QueueSortKey>("updated_desc");
  const [page, setPage] = useState(1);

  /** 最新 jobs 的旁路引用：轮询决策每次续排时实时读取，不受闭包过期影响。 */
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
  jobsRef.current = jobs;

  /** 修复 1：任务状态轮询（此前本页只有挂载时一次 fetch，任务完成后界面不刷新）。
   *  失败时抛错（由轮询层上报并继续重试），保持旧数据。 */
  const loadJobs = useCallback(async () => {
    const response = await fetch("/api/jobs", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`/api/jobs 返回 HTTP ${response.status}`);
    }
    const payload = (await response.json()) as JobSummaryRecord[] | { items?: JobSummaryRecord[] };
    const next = Array.isArray(payload) ? payload : payload.items ?? [];
    // 必须在 setJobs 之外同步更新旁路引用：轮询决策在 fetch 返回的同一微任务里
    // 立即执行（早于 React 重渲染），走渲染期赋值会让终态判定晚一个周期、
    // 多打一次后端。
    jobsRef.current = next;
    setJobs(next);
  }, []);

  const polling = useJobPolling({
    fetcher: loadJobs,
    decide: useCallback(() => resolvePollingDecision(jobsRef.current), []),
  });

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

  // 筛选变化后页码重置到第一页（否则可能停留在超出范围的空页）
  const updateFilters = useCallback((patch: Partial<QueuePageFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
    setPage(1);
  }, []);

  const handleReanalyze = useCallback(
    async (jobId: string) => {
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/reanalyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "dual", use_local_rules: true, use_ai_assist: true }),
        });
        if (response.ok) {
          await loadJobs();
        }
      } catch {
        // 静默失败：队列行会在下一次刷新时反映真实状态。
      }
    },
    [loadJobs],
  );

  return (
    <div className="p-8" data-testid="gbc-queue-page">
      <SectionTitle
        title="处理队列"
        desc={`全部任务的处理状态与阶段进度。当前正在分析 ${analyzingCount} 个任务（与侧边栏角标同口径）。`}
      />

      <div className="mt-6 mb-3 flex flex-wrap items-center gap-2">
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
          正在加载处理队列…
        </div>
      ) : (
        <WorkbenchQueueTable
          jobs={pagination.items}
          onReanalyze={(jobId) => void handleReanalyze(jobId)}
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
