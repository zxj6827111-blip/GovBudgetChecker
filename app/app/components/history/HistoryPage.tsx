/**
 * HistoryPage：Task 8.2 任务历史页——终态任务检索与报告下载。
 *
 * 复用：
 * - WorkbenchQueueTable（通过新增的 renderRowActions 可选 prop 在操作列
 *   渲染报告下载入口，默认行为不变，工作台/处理队列页零影响）；
 * - 与处理队列页完全一致的筛选器（filterHistoryJobs = 终态过滤 +
 *   filterQueueJobs 六维筛选）与排序、分页逻辑（queuePageAdapters）；
 * - OrganizationFilterSelect。
 *
 * 报告下载接既有 /api/reports/download（支持 pdf/cvs/json 三种格式，
 * require_job_access 鉴权由后端负责，前端只构造 URL）。
 * 终态口径：completed / review_required / failed（isUiTaskFinished 同源），
 * queued / processing 还在跑，不属于历史。
 */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SectionTitle } from "@/components/ui";
import { resolvePollingDecision } from "@/lib/jobPolling";
import type { JobSummaryRecord } from "@/lib/uiAdapters";

import {
  DEFAULT_QUEUE_FILTERS,
  QUEUE_SORT_LABELS,
  QUEUE_STAGE_FILTER_LABELS,
  paginateJobs,
  sortQueueJobs,
  type QueuePageFilters,
  type QueueReportKindFilter,
  type QueueSortKey,
  type QueueStageFilter,
} from "../queue/queuePageAdapters";
import { OrganizationFilterSelect } from "../workspace/OrganizationFilterSelect";
import { PollRefreshStatus } from "../workspace/PollRefreshStatus";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import { collectAvailableYears, type WorkbenchStatusFilter } from "../workspace/workbenchAdapters";
import {
  REPORT_DOWNLOAD_FORMATS,
  buildReportDownloadUrl,
  countTerminalJobs,
  filterHistoryJobs,
} from "./historyPageAdapters";

const PAGE_SIZE = 20;

const STATUS_FILTER_OPTIONS: Array<{ value: WorkbenchStatusFilter; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "review_required", label: "待人工复核" },
  { value: "failed", label: "处理失败" },
  { value: "completed", label: "已完成" },
  // 注意：analyzing 不在选项里——任务历史只检索终态任务（isUiTaskFinished），
  // "正在处理"的任务请去处理队列页看。
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

export function HistoryPage() {
  const [jobs, setJobs] = useState<JobSummaryRecord[] | null>(null);
  const [filters, setFilters] = useState<QueuePageFilters>(DEFAULT_QUEUE_FILTERS);
  const [sortKey, setSortKey] = useState<QueueSortKey>("updated_desc");
  const [page, setPage] = useState(1);

  /** 最新 jobs 的旁路引用：轮询决策每次续排时实时读取，不受闭包过期影响。 */
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
  jobsRef.current = jobs;

  /** 修复 1：任务历史页同样接轮询——终态任务列表需要感知"还在跑的任务跑完了"
   *  （跑完才会出现在历史里）。失败时抛错（由轮询层上报并继续重试），保持旧数据。 */
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

  const availableYears = useMemo(() => collectAvailableYears(jobs), [jobs]);
  const terminalCount = useMemo(() => countTerminalJobs(jobs), [jobs]);

  const filteredJobs = useMemo(() => filterHistoryJobs(jobs, filters), [jobs, filters]);
  const sortedJobs = useMemo(() => sortQueueJobs(filteredJobs, sortKey), [filteredJobs, sortKey]);
  const pagination = useMemo(() => paginateJobs(sortedJobs, page, PAGE_SIZE), [sortedJobs, page]);

  const updateFilters = useCallback((patch: Partial<QueuePageFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
    setPage(1);
  }, []);

  const renderDownloadActions = useCallback((job: JobSummaryRecord) => {
    const jobId = String(job.job_id ?? "");
    return (
      <span
        className="inline-flex items-center gap-1"
        data-testid={`gbc-history-download-${jobId}`}
      >
        {REPORT_DOWNLOAD_FORMATS.map((format) => (
          <a
            key={format}
            href={buildReportDownloadUrl(jobId, format)}
            aria-label={`下载任务 ${jobId} 的 ${format.toUpperCase()} 报告`}
            data-testid={`gbc-history-download-${jobId}-${format}`}
            className="rounded-md border border-border px-2 py-1 text-xs font-medium text-slate-600 transition-colors hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            {format.toUpperCase()}
          </a>
        ))}
      </span>
    );
  }, []);

  return (
    <div className="p-8" data-testid="gbc-history-page">
      <SectionTitle
        title="任务历史"
        desc={`已完成、待人工复核与失败任务的检索；每个任务可下载 PDF / CSV / JSON 格式报告（当前共 ${terminalCount} 个终态任务）。`}
      />

      <div className="mt-6 mb-3 flex flex-wrap items-center gap-2">
        <input
          value={filters.keyword}
          onChange={(event) => updateFilters({ keyword: event.target.value })}
          placeholder="搜索文件名、组织或任务 ID"
          data-testid="gbc-history-search"
          className="min-w-[220px] flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        <select
          value={filters.status}
          onChange={(event) => updateFilters({ status: event.target.value as WorkbenchStatusFilter })}
          data-testid="gbc-history-status-filter"
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
          data-testid="gbc-history-year-filter"
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
          data-testid="gbc-history-kind-filter"
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
          data-testid="gbc-history-stage-filter"
          aria-label="按失败阶段筛选"
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
          data-testid="gbc-history-org-filter"
        />
        <select
          value={sortKey}
          onChange={(event) => {
            setSortKey(event.target.value as QueueSortKey);
            setPage(1);
          }}
          data-testid="gbc-history-sort"
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
        <span data-testid="gbc-history-result-count">
          共 {pagination.total} 个终态任务（总 {jobs?.length ?? 0} 个任务）
        </span>
        <PollRefreshStatus
          lastSyncedAt={polling.lastSyncedAt}
          lastErrorMessage={polling.lastErrorMessage}
          isRefreshing={polling.isManualRefreshing}
          onRefresh={polling.refreshNow}
          testIdPrefix="gbc-history-refresh"
        />
        {pagination.pageCount > 1 ? (
          <span data-testid="gbc-history-pagination-info">
            第 {pagination.page} / {pagination.pageCount} 页
          </span>
        ) : null}
      </div>

      {jobs === null ? (
        <div
          className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
          data-testid="gbc-history-loading"
        >
          正在加载任务历史…
        </div>
      ) : (
        <WorkbenchQueueTable
          jobs={pagination.items}
          onReanalyze={() => {
            // 历史页的操作列渲染下载入口（renderRowActions），此回调不会被触发，
            // 但 prop 保持必填以兼容组件签名。
          }}
          renderRowActions={renderDownloadActions}
        />
      )}

      {pagination.pageCount > 1 ? (
        <div className="mt-4 flex items-center justify-end gap-2" data-testid="gbc-history-pagination">
          <button
            type="button"
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            disabled={pagination.page <= 1}
            data-testid="gbc-history-prev-page"
            className="rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            上一页
          </button>
          <button
            type="button"
            onClick={() => setPage((prev) => Math.min(pagination.pageCount, prev + 1))}
            disabled={pagination.page >= pagination.pageCount}
            data-testid="gbc-history-next-page"
            className="rounded-md border border-border bg-white px-3 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            下一页
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default HistoryPage;
