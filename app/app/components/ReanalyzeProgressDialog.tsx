"use client";

import { useEffect } from "react";

type ReanalyzeCreatedItem = {
  source_job_id?: string | null;
  job_id?: string | null;
  status?: string | null;
  dispatch?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  scope_id?: string | null;
  scope_name?: string | null;
  scope_level?: string | null;
};

type ReanalyzeSkippedItem = {
  source_job_id?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  status?: string | null;
  reason?: string | null;
  scope_id?: string | null;
  scope_name?: string | null;
  scope_level?: string | null;
};

type ReanalyzeFailedItem = {
  source_job_id?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  status_code?: number | null;
  detail?: string | null;
  scope_id?: string | null;
  scope_name?: string | null;
  scope_level?: string | null;
};

export type ReanalyzeBatchPayload = {
  status?: string | null;
  include_active?: boolean | null;
  latest_per_department?: boolean | null;
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
  job_id: string;
  status?: string | null;
  progress?: number | null;
  current_stage?: string | null;
  stage?: string | null;
  error?: string | null;
  failure_reason?: string | null;
  ts?: number | null;
};

interface ReanalyzeProgressDialogProps {
  isOpen: boolean;
  batch: ReanalyzeBatchPayload | null;
  liveStatuses: Record<string, ReanalyzeLiveStatus>;
  onClose: () => void;
}

const TERMINAL_STATUSES = new Set(["done", "completed", "error", "failed"]);

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
      return "emerald";
    case "error":
    case "failed":
      return "rose";
    case "processing":
    case "running":
      return "sky";
    default:
      return "amber";
  }
}

function getStatusLabel(status?: string | null) {
  switch (normalizeStatus(status)) {
    case "queued":
      return "排队中";
    case "uploaded":
      return "已创建";
    case "processing":
      return "处理中";
    case "running":
      return "运行中";
    case "done":
    case "completed":
      return "已完成";
    case "error":
      return "失败";
    case "failed":
      return "异常";
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
      return "属于下属单位报告，按部门模式已跳过";
    case "unresolved_department":
      return "未识别到所属部门";
    default:
      return reason || "已跳过";
  }
}

function SectionCard({
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
  const toneClass =
    tone === "emerald"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : tone === "sky"
        ? "border-sky-200 bg-sky-50 text-sky-800"
        : tone === "rose"
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : tone === "amber"
            ? "border-amber-200 bg-amber-50 text-amber-800"
            : "border-slate-200 bg-slate-50 text-slate-800";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
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
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen || !batch) {
    return null;
  }

  const created = batch.created || [];
  const skipped = batch.skipped || [];
  const failed = batch.failed || [];

  const liveItems = created.map((item) => {
    const jobId = String(item.job_id || "");
    return {
      ...item,
      live: jobId ? liveStatuses[jobId] : undefined,
      effectiveStatus: jobId ? liveStatuses[jobId]?.status || item.status || "queued" : item.status || "queued",
    };
  });

  const doneCount = liveItems.filter((item) => {
    const status = normalizeStatus(item.effectiveStatus);
    return status === "done" || status === "completed";
  }).length;
  const runningCount = liveItems.filter((item) => {
    const status = normalizeStatus(item.effectiveStatus);
    return status === "processing" || status === "running";
  }).length;
  const queuedCount = liveItems.filter((item) => {
    const status = normalizeStatus(item.effectiveStatus);
    return !TERMINAL_STATUSES.has(status) && status !== "processing" && status !== "running";
  }).length;
  const errorCount = liveItems.filter((item) => {
    const status = normalizeStatus(item.effectiveStatus);
    return status === "error" || status === "failed";
  }).length;

  return (
    <div className="fixed inset-0 z-[180] flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-[28px] border border-white/60 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-indigo-500">批量重分析</div>
            <h2 className="mt-1 text-2xl font-bold text-slate-900">按组织重分析进度</h2>
            <p className="mt-2 text-sm text-slate-600">
              会在每个部门和单位的最新报告原任务上直接刷新并重新分析，不再创建新任务；默认继承原任务的 AI / 本地规则开关。
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900"
          >
            关闭
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5">
          <div className="grid gap-3 md:grid-cols-5">
            <SectionCard title="已刷新" value={batch.created_count} description="成功刷新并重跑的原任务" tone="slate" />
            <SectionCard title="已完成" value={doneCount} description="重分析已经完成" tone="emerald" />
            <SectionCard title="处理中" value={runningCount} description="正在运行的任务" tone="sky" />
            <SectionCard title="等待中" value={queuedCount} description="尚未完成的任务" tone="amber" />
            <SectionCard title="失败" value={errorCount + (batch.failed_count || 0)} description="刷新失败或运行失败" tone="rose" />
          </div>

          <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            共请求 {formatCount(batch.requested_count)} 个历史任务，选中 {formatCount(batch.selected_count)} 个组织最新报告，
            刷新 {formatCount(batch.created_count)} 个原任务，跳过 {formatCount(batch.skipped_count)} 个。
          </div>

          <section className="mt-6">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">实时进度</h3>
                <p className="mt-1 text-sm text-slate-500">下面会持续刷新每个组织对应的原任务刷新状态。</p>
              </div>
              <div className="rounded-full bg-indigo-100 px-3 py-1 text-sm font-medium text-indigo-700">
                {doneCount}/{created.length} 完成
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {liveItems.length > 0 ? (
                liveItems.map((item, index) => {
                  const tone = getStatusTone(item.effectiveStatus);
                  const toneClass =
                    tone === "emerald"
                      ? "border-emerald-200 bg-emerald-50"
                      : tone === "rose"
                        ? "border-rose-200 bg-rose-50"
                        : tone === "sky"
                          ? "border-sky-200 bg-sky-50"
                          : "border-amber-200 bg-amber-50";
                  const stageText = item.live?.current_stage || item.live?.stage || "";
                  const progressValue =
                    typeof item.live?.progress === "number" ? Math.max(0, Math.min(100, item.live.progress)) : null;
                  return (
                    <div key={`${item.job_id || "created"}-${index}`} className={`rounded-3xl border p-4 ${toneClass}`}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-semibold text-slate-900">
                            {item.scope_name || item.department_name || "未识别组织"}
                          </div>
                          <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                            <span>
                              范围：
                              {item.scope_level === "department"
                                ? "部门"
                                : item.scope_level === "unit"
                                  ? "单位"
                                  : "未知"}
                            </span>
                            <span>原任务：{item.source_job_id || "--"}</span>
                            <span>刷新任务：{item.job_id || "--"}</span>
                          </div>
                          {stageText ? (
                            <div className="mt-2 text-sm text-slate-600">当前阶段：{stageText}</div>
                          ) : null}
                          {progressValue !== null ? (
                            <div className="mt-3">
                              <div className="h-2 overflow-hidden rounded-full bg-white/70">
                                <div
                                  className="h-full rounded-full bg-indigo-500 transition-all"
                                  style={{ width: `${progressValue}%` }}
                                />
                              </div>
                              <div className="mt-1 text-xs text-slate-500">进度 {progressValue}%</div>
                            </div>
                          ) : null}
                          {item.live?.error || item.live?.failure_reason ? (
                            <div className="mt-2 text-sm text-rose-700">
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
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
                  当前没有被刷新的重分析任务。
                </div>
              )}
            </div>
          </section>

          {skipped.length > 0 ? (
            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-900">未刷新的组织</h3>
              <div className="mt-4 space-y-3">
                {skipped.map((item, index) => (
                  <div key={`${item.source_job_id || "skip"}-${index}`} className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-slate-900">{item.scope_name || item.department_name || "未识别组织"}</div>
                        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                          <span>
                            范围：
                            {item.scope_level === "department"
                              ? "部门"
                              : item.scope_level === "unit"
                                ? "单位"
                                : "未知"}
                          </span>
                          <span>原任务：{item.source_job_id || "--"}</span>
                        </div>
                      </div>
                      <div className="rounded-full bg-white/80 px-3 py-1 text-xs font-medium text-amber-700">
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
              <h3 className="text-lg font-semibold text-slate-900">刷新失败</h3>
              <div className="mt-4 space-y-3">
                {failed.map((item, index) => (
                  <div key={`${item.source_job_id || "failed"}-${index}`} className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-sm font-semibold text-rose-800">{item.scope_name || item.department_name || "未识别组织"}</div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-rose-700">
                      <span>
                        范围：
                        {item.scope_level === "department"
                          ? "部门"
                          : item.scope_level === "unit"
                            ? "单位"
                            : "未知"}
                      </span>
                      <span>原任务：{item.source_job_id || "--"}</span>
                    </div>
                    <div className="mt-2 text-sm text-rose-700">
                      {item.status_code ? `HTTP ${item.status_code} ` : ""}
                      {item.detail || "刷新失败"}
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
