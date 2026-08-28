/**
 * WorkbenchPage：Task 4，1:1 还原 `01-workbench-overview.png`。
 *
 * 数据来源与鉴权边界：
 * - `/api/jobs`：require_login 即可访问，全部审核员可见——五张 KPI 卡、处理队列表、
 *   覆盖率聚合、覆盖率不足告警均只依赖这个端点，因此普通审核员也能看到这些内容。
 * - `/api/metrics`：admin 会话或抓取令牌才能访问。unknown 比例告警需要它；
 *   非管理员请求会拿到 401/403，此时该告警必须静默跳过（不触发、不报错），
 *   不能因为拿不到 metrics 就让整页崩溃或弹错误提示——这与"最近活动"面板的
 *   降级原则一致，但呈现方式不同：活动面板整块显示"仅管理员可查看"，
 *   而这里只是"少一条可能出现的告警"（普通审核员本来就不该关心 unknown 比例，
 *   它更接近运营口径的指标）。
 */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button, Metric, SectionTitle } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";

import { OrganizationFilterSelect } from "./OrganizationFilterSelect";
import { PollRefreshStatus } from "./PollRefreshStatus";
import { useJobPolling } from "./useJobPolling";
import { WorkbenchActivityPanel } from "./WorkbenchActivityPanel";
import { WorkbenchAlertsPanel } from "./WorkbenchAlertsPanel";
import { WorkbenchQueueTable } from "./WorkbenchQueueTable";
import {
  aggregatePageCoverage,
  collectAvailableYears,
  computeWorkbenchKpiCounts,
  deriveQualityAlerts,
  filterWorkbenchQueue,
  formatCoveragePercentText,
  type QualityAlert,
  type WorkbenchStatusFilter,
} from "./workbenchAdapters";
import { resolvePollingDecision } from "@/lib/jobPolling";
import { ANALYZE_START_REQUEST_BODY } from "./uploadCenterAdapters";

/** 页面覆盖率质量门禁阈值：与 api/main.py 的 PAGE_COVERAGE_MIN_RATIO 默认值同源（0.8）。
 *  该常量本身不是新造的口径——PAGE_COVERAGE_MIN_RATIO 是环境变量，前端拿不到后端的
 *  运行时环境变量值，只能取代码里写明的默认值；若部署时改了该环境变量，此处会与
 *  实际生效阈值不一致，这是本轮已知的局限（见交付说明"未验证部分"）。 */
const PAGE_COVERAGE_MIN_RATIO = 0.8;
/** unknown 文档类型比例阈值：docs/OBSERVABILITY_AND_ALERTS.md 的
 *  govbudget_unknown_report_kind_ratio 告警阈值（0.05），不得另立口径。 */
const UNKNOWN_REPORT_KIND_MAX_RATIO = 0.05;
/** 比例类指标最小样本量护栏：同一份文档"所有比例类指标的分母都是被扫描到的任务产物总数，
 *  上线初期样本少时波动大，建议同时要求 jobs_total >= 20 才触发比例类告警"。 */
const MIN_SAMPLE_SIZE_FOR_RATIO_ALERTS = 20;

interface MetricsResponse {
  jobs?: { total?: number };
  quality?: { unknown_report_kind?: { ratio?: number | null } };
}

const STATUS_FILTER_OPTIONS: Array<{ value: WorkbenchStatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "review_required", label: "待人工复核" },
  { value: "pending_analysis", label: "待分析" },
  { value: "analyzing", label: "正在处理" },
  { value: "failed", label: "处理失败" },
  { value: "completed", label: "已完成" },
];

export function WorkbenchPage() {
  const [jobs, setJobs] = useState<JobSummaryRecord[] | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [keyword, setKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState<WorkbenchStatusFilter>("all");
  const [yearFilter, setYearFilter] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [isRetryingAll, setIsRetryingAll] = useState(false);

  /** 最新 jobs 的旁路引用：轮询决策每次续排时实时读取，不受闭包过期影响。 */
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
  jobsRef.current = jobs;

  /** 修复 1：任务状态轮询。失败时抛错（由轮询层上报并继续重试），保持旧数据。 */
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

  const loadMetrics = useCallback(async () => {
    try {
      const response = await fetch("/api/metrics", { cache: "no-store" });
      if (!response.ok) {
        // 非管理员/无抓取令牌时会是 401/403，这是预期中的降级路径，
        // 不设 error 态，只是让 unknown 比例告警缺失这一项输入。
        return;
      }
      const payload = (await response.json()) as MetricsResponse;
      setMetrics(payload);
    } catch {
      // 同上：保持 metrics=null。
    }
  }, []);

  useEffect(() => {
    // metrics 不参与轮询（管理员聚合指标变化慢，且非管理员必然 403），
    // 只在挂载与手动刷新时拉一次；jobs 的定时轮询由 useJobPolling 负责。
    void loadMetrics();
  }, [loadMetrics]);

  const polling = useJobPolling({
    fetcher: useCallback(async () => {
      await loadJobs();
      await loadMetrics();
    }, [loadJobs, loadMetrics]),
    decide: useCallback(() => resolvePollingDecision(jobsRef.current), []),
  });

  const kpiCounts = useMemo(() => computeWorkbenchKpiCounts(jobs), [jobs]);
  const coverageAggregate = useMemo(() => aggregatePageCoverage(jobs), [jobs]);
  const availableYears = useMemo(() => collectAvailableYears(jobs), [jobs]);

  const qualityAlerts = useMemo<QualityAlert[] | null>(() => {
    if (jobs === null) {
      return null;
    }
    return deriveQualityAlerts({
      jobs,
      unknownReportKindRatio: metrics?.quality?.unknown_report_kind?.ratio ?? null,
      metricsJobsTotal: metrics?.jobs?.total ?? null,
      pageCoverageMinRatio: PAGE_COVERAGE_MIN_RATIO,
      unknownReportKindMaxRatio: UNKNOWN_REPORT_KIND_MAX_RATIO,
      minSampleSizeForRatioAlerts: MIN_SAMPLE_SIZE_FOR_RATIO_ALERTS,
    });
  }, [jobs, metrics]);

  const filteredJobs = useMemo(
    () =>
      filterWorkbenchQueue(jobs, {
        keyword,
        status: statusFilter,
        year: yearFilter,
        organizationId,
      }),
    [jobs, keyword, statusFilter, yearFilter, organizationId],
  );

  const handleAlertClick = useCallback((alert: QualityAlert) => {
    if (alert.filterHint.qualityStatus === "review_required") {
      setStatusFilter("review_required");
    }
    // unknown 类型告警目前没有对应的"文档类型"筛选维度（原型图队列表也没有该筛选器），
    // 因此点击只做已支持的筛选跳转，不新增一个筛选维度去凑这条告警的可跳转性。
  }, []);

  const handleReanalyzeOne = useCallback(async (jobId: string) => {
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
      // 静默失败：不弹全局报错遮罩，队列行会在下一次刷新时反映真实状态。
    }
  }, [loadJobs]);

  /** 修复 3：对 uploaded（待分析）任务启动首次分析——与上传触发分析同链路。 */
  const handleStartAnalysis = useCallback(
    async (jobId: string) => {
      try {
        const response = await fetch(`/api/analyze/${encodeURIComponent(jobId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(ANALYZE_START_REQUEST_BODY),
        });
        await loadJobs();
        if (!response.ok) {
          // 静默失败与既有重试入口一致：队列行在下一次刷新时反映真实状态。
        }
      } catch {
        // 同上。
      }
    },
    [loadJobs],
  );

  const handleRetryAll = useCallback(async () => {
    setIsRetryingAll(true);
    try {
      const response = await fetch("/api/jobs/reanalyze-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: false }),
      });
      if (response.ok) {
        await loadJobs();
      }
    } catch {
      // 同上，静默失败。
    } finally {
      setIsRetryingAll(false);
    }
  }, [loadJobs]);

  return (
    <div className="p-8" data-testid="gbc-workbench-page">
      <SectionTitle
        title="工作台总览"
        desc="集中查看处理队列、需人工复核任务和系统质量告警。"
      />

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Metric
          label="待人工复核"
          corner="今日"
          value={kpiCounts?.reviewRequired}
          tone="warning"
          data-testid="gbc-workbench-kpi-review-required"
        />
        <Metric
          label="正在处理"
          corner="实时"
          value={kpiCounts?.processing}
          tone="info"
          data-testid="gbc-workbench-kpi-processing"
        />
        <Metric
          label="处理失败"
          corner="24 小时"
          value={kpiCounts?.failed}
          tone="danger"
          data-testid="gbc-workbench-kpi-failed"
        />
        <Metric
          label="本周已完成"
          corner="累计"
          value={kpiCounts?.completedThisWeek}
          tone="success"
          data-testid="gbc-workbench-kpi-completed"
        />
        <Metric
          label="页面覆盖率"
          corner="质量门禁"
          value={coverageAggregate === null ? null : formatCoveragePercentText(coverageAggregate)}
          desc={
            coverageAggregate
              ? `样本 ${coverageAggregate.sampleSize} 个任务（另有 ${coverageAggregate.excludedCount} 个无覆盖率字段，未纳入聚合）`
              : "暂无可聚合的覆盖率数据"
          }
          tone="primary"
          data-testid="gbc-workbench-kpi-coverage"
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜索文件名、组织或任务 ID"
              data-testid="gbc-workbench-search"
              className="min-w-[220px] flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            />
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as WorkbenchStatusFilter)}
              data-testid="gbc-workbench-status-filter"
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              {STATUS_FILTER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={yearFilter}
              onChange={(event) => setYearFilter(event.target.value)}
              data-testid="gbc-workbench-year-filter"
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
            <OrganizationFilterSelect value={organizationId} onChange={setOrganizationId} />
            <Button
              variant="secondary"
              onClick={() => void handleRetryAll()}
              disabled={isRetryingAll}
              data-testid="gbc-workbench-retry-all"
            >
              {isRetryingAll ? "重试中…" : "批量重试"}
            </Button>
            <PollRefreshStatus
              lastSyncedAt={polling.lastSyncedAt}
              lastErrorMessage={polling.lastErrorMessage}
              isRefreshing={polling.isManualRefreshing}
              onRefresh={polling.refreshNow}
              testIdPrefix="gbc-workbench-refresh"
            />
          </div>

          {jobs === null ? (
            <div
              className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
              data-testid="gbc-workbench-queue-loading"
            >
              正在加载处理队列…
            </div>
          ) : (
            <WorkbenchQueueTable
              jobs={filteredJobs}
              onReanalyze={(jobId) => void handleReanalyzeOne(jobId)}
              onStartAnalysis={(jobId) => void handleStartAnalysis(jobId)}
            />
          )}
        </div>

        <div className="space-y-6">
          <WorkbenchAlertsPanel alerts={qualityAlerts} onAlertClick={handleAlertClick} />
          <WorkbenchActivityPanel />
        </div>
      </div>
    </div>
  );
}

export default WorkbenchPage;
