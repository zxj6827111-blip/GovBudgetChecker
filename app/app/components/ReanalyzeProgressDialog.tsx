"use client";
import AnalysisCheckProgress from "@/components/AnalysisCheckProgress";

import { useEffect } from "react";

type ReanalyzeCreatedItem = {
  source_job_id?: string | null;
  job_id?: string | null;
  status?: string | null;
  department_name?: string | null;
  scope_name?: string | null;
  scope_level?: string | null;
  check_method?: string | null;
  inheritance_warnings?: string[] | null;
};

type ReanalyzeSkippedItem = {
  source_job_id?: string | null;
  department_name?: string | null;
  scope_name?: string | null;
  reason?: string | null;
  scope_level?: string | null;
};

type ReanalyzeFailedItem = {
  source_job_id?: string | null;
  department_name?: string | null;
  scope_name?: string | null;
  status_code?: number | null;
  detail?: string | null;
  scope_level?: string | null;
};

export type ReanalyzeBatchPayload = {
  requested_count?: number | null;
  selected_count?: number | null;
  created_count?: number | null;
  skipped_count?: number | null;
  failed_count?: number | null;
  created?: ReanalyzeCreatedItem[] | null;
  skipped?: ReanalyzeSkippedItem[] | null;
  failed?: ReanalyzeFailedItem[] | null;
};

export type ReanalyzeLiveStatus = {
  check_progress?: unknown;
  job_id: string;
  status?: string | null;
  progress?: number | null;
  current_stage?: string | null;
  stage?: string | null;
  error?: string | null;
  failure_reason?: string | null;
};

interface ReanalyzeProgressDialogProps {
  isOpen: boolean;
  batch: ReanalyzeBatchPayload | null;
  liveStatuses: Record<string, ReanalyzeLiveStatus>;
  onClose: () => void;
}

const TERMINAL_STATUSES = new Set(["done", "completed", "review_required", "degraded", "error", "failed", "cancelled"]);

function formatCount(value?: number | null) {
  return typeof value === "number" ? value.toLocaleString() : "--";
}

function normalizeStatus(value?: string | null) {
  return String(value || "queued").trim().toLowerCase();
}

function getStatusTone(status?: string | null) {
  switch (normalizeStatus(status)) {
    case "done":
    case "completed":
      return "border-success-200 bg-success-50";
    case "error":
    case "failed":
      return "border-danger-200 bg-danger-50";
    case "processing":
    case "running":
      return "border-primary-200 bg-primary-50";
    default:
      return "border-warning-200 bg-warning-50";
  }
}

function getStatusLabel(status?: string | null) {
  switch (normalizeStatus(status)) {
    case "queued":
      return "排队中";
    case "uploaded":
      return "已创建";
    case "processing":
    case "running":
      return "处理中";
    case "done":
    case "completed":
      return "已完成";
    case "error":
    case "failed":
      return "失败";
    default:
      return status || "未知";
  }
}

function getReasonLabel(reason?: string | null) {
  switch ((reason || "").trim()) {
    case "active_analysis":
      return "已有任务正在分析";
    case "not_latest_in_department":
      return "不是该部门最新报告";
    case "not_latest_in_scope":
      return "不是该组织最新报告";
    case "subordinate_unit_report":
      return "属于下属单位报告，已按部门模式跳过";
    case "unresolved_department":
      return "未识别到所属部门";
    default:
      return reason || "已跳过";
  }
}

function Card({
  title,
  value,
  description,
  tone,
}: {
  title: string;
  value?: number | null;
  description: string;
  tone: "slate" | "emerald" | "sky" | "rose" | "amber";
}) {
  const className =
    tone === "emerald"
      ? "border-success-200 bg-success-50 text-success-800"
      : tone === "sky"
        ? "border-primary-200 bg-primary-50 text-primary-800"
        : tone === "rose"
          ? "border-danger-200 bg-danger-50 text-danger-800"
          : tone === "amber"
            ? "border-warning-200 bg-warning-50 text-warning-800"
            : "border-border bg-slate-50 text-slate-800";

  return (
    <div className={`rounded-md border p-4 ${className}`}>
      <div className="text-xs font-semibold uppercase tracking-[0.18em] opacity-70">{title}</div>
      <div className="mt-2 text-3xl font-bold tabular-nums">{formatCount(value)}</div>
      <div className="mt-1 text-sm opacity-80">{description}</div>
    </div>
  );
}

export default function ReanalyzeProgressDialog({
  isOpen,
  batch,
  liveStatuses,
  onClose,
}: ReanalyzeProgressDialogProps) {
  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !batch) {
    return null;
  }

  const created = batch.created || [];
  const skipped = batch.skipped || [];
  const failed = batch.failed || [];

  const liveItems = created.map((item) => {
    const jobId = String(item.job_id || "");
    const live = jobId ? liveStatuses[jobId] : undefined;
    return {
      ...item,
      live,
      effectiveStatus: live?.status || item.status || "queued",
    };
  });

  const doneCount = liveItems.filter((item) => ["done", "completed"].includes(normalizeStatus(item.effectiveStatus))).length;
  const runningCount = liveItems.filter((item) => ["processing", "running"].includes(normalizeStatus(item.effectiveStatus))).length;
  const queuedCount = liveItems.filter((item) => {
    const status = normalizeStatus(item.effectiveStatus);
    return !TERMINAL_STATUSES.has(status) && !["processing", "running"].includes(status);
  }).length;
  const errorCount = liveItems.filter((item) => ["error", "failed"].includes(normalizeStatus(item.effectiveStatus))).length;

  return (
    <div data-testid="reanalyze-progress-dialog" role="dialog" aria-modal="true" aria-label="重分析进度" className="fixed inset-0 z-[180] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-card border border-white/60 bg-white shadow-dialog">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-6 py-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-primary-500">重分析进度</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-900">按组织重分析状态</h2>
            <p className="mt-2 text-sm text-slate-600">
              系统会对每个组织当前最新报告发起重分析，并在这里持续刷新处理状态。
            </p>
          </div>
          <button
            type="button"
            data-testid="reanalyze-progress-close"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
          >
            关闭
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5">
          <div className="grid gap-3 md:grid-cols-5">
            <Card title="已触发" value={batch.created_count} description="创建的重分析任务" tone="slate" />
            <Card title="已完成" value={doneCount} description="已完成的任务" tone="emerald" />
            <Card title="处理中" value={runningCount} description="正在执行的任务" tone="sky" />
            <Card title="等待中" value={queuedCount} description="尚未结束的任务" tone="amber" />
            <Card title="失败" value={errorCount + (batch.failed_count || 0)} description="刷新失败或运行失败" tone="rose" />
          </div>

          <div className="mt-4 rounded-md border border-border bg-slate-50 px-4 py-3 text-sm text-slate-600">
            共请求 {formatCount(batch.requested_count)} 个历史任务，筛出 {formatCount(batch.selected_count)} 个最新报告，
            创建 {formatCount(batch.created_count)} 个重分析任务，跳过 {formatCount(batch.skipped_count)} 个。
          </div>

          {created.some((item) => (item.inheritance_warnings || []).length > 0) ? (
            <div className="mt-3 rounded-md border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800" data-testid="reanalyze-inheritance-warnings">
              <p className="font-medium">继承提示（不影响已启动的任务）：</p>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {created.flatMap((item) =>
                  (item.inheritance_warnings || []).map((warning, index) => (
                    <li key={`${item.job_id || item.source_job_id}-${index}`}>
                      {item.scope_name || item.department_name || item.job_id}：{warning}
                    </li>
                  )),
                )}
              </ul>
            </div>
          ) : null}

          <section className="mt-6">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">实时状态</h3>
                <p className="mt-1 text-sm text-slate-500">下面会持续展示每个组织的当前进度。</p>
              </div>
              <div className="rounded-full bg-primary-100 px-3 py-1 text-sm font-medium text-primary-700">
                {doneCount}/{created.length} 完成
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {liveItems.length > 0 ? (
                liveItems.map((item, index) => {
                  const progressValue =
                    typeof item.live?.progress === "number" ? Math.max(0, Math.min(100, item.live.progress)) : null;
                  return (
                    <div
                      key={`${item.job_id || "created"}-${index}`}
                      className={`rounded-md border p-4 ${getStatusTone(item.effectiveStatus)}`}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-semibold text-slate-900">
                            {item.scope_name || item.department_name || "未识别组织"}
                          </div>
                          <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                            <span>层级：{item.scope_level === "unit" ? "单位" : "部门"}</span>
                            <span>原任务：{item.source_job_id || "--"}</span>
                            <span>新任务：{item.job_id || "--"}</span>
                          </div>
                          {item.live?.current_stage || item.live?.stage ? (
                            <div className="mt-2 text-sm text-slate-600">
                              当前阶段：{item.live?.current_stage || item.live?.stage}
                            </div>
                          ) : null}
                          {progressValue !== null ? (
                            <div className="mt-3">
                              <div className="h-2 overflow-hidden rounded-full bg-white/70">
                                <div className="h-full rounded-full bg-primary-500 transition-all" style={{ width: `${progressValue}%` }} />
                              </div>
                              <div className="mt-1 text-xs text-slate-500">进度 {progressValue}%</div>
                            </div>
                          ) : null}
                          <AnalysisCheckProgress value={item.live?.check_progress} />
                          {item.live?.error || item.live?.failure_reason ? (
                            <div className="mt-2 text-sm text-danger-700">
                              {item.live.error || item.live.failure_reason}
                            </div>
                          ) : null}
                        </div>
                        <div className="rounded-full bg-white/80 px-3 py-1 text-sm font-medium text-slate-700">
                          {getStatusLabel(item.effectiveStatus)}
                        </div>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
                  当前没有正在追踪的重分析任务。
                </div>
              )}
            </div>
          </section>

          {skipped.length > 0 ? (
            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-900">跳过项</h3>
              <div className="mt-4 space-y-3">
                {skipped.map((item, index) => (
                  <div key={`${item.source_job_id || "skip"}-${index}`} className="rounded-md border border-warning-200 bg-warning-50 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-slate-900">
                          {item.scope_name || item.department_name || "未识别组织"}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                          原任务：{item.source_job_id || "--"} / 层级：{item.scope_level === "unit" ? "单位" : "部门"}
                        </div>
                      </div>
                      <div className="rounded-full bg-white/80 px-3 py-1 text-xs font-medium text-warning-700">
                        {getReasonLabel(item.reason)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {failed.length > 0 ? (
            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-900">失败项</h3>
              <div className="mt-4 space-y-3">
                {failed.map((item, index) => (
                  <div key={`${item.source_job_id || "failed"}-${index}`} className="rounded-md border border-danger-200 bg-danger-50 p-4">
                    <div className="text-sm font-semibold text-danger-800">
                      {item.scope_name || item.department_name || "未识别组织"}
                    </div>
                    <div className="mt-1 text-xs text-danger-700">
                      原任务：{item.source_job_id || "--"} / 层级：{item.scope_level === "unit" ? "单位" : "部门"}
                    </div>
                    <div className="mt-2 text-sm text-danger-700">
                      {item.status_code ? `HTTP ${item.status_code} ` : ""}
                      {item.detail || "任务执行失败"}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
