"use client";

import { useEffect } from "react";

type MatchOrganization = {
  organization_id?: string | null;
  organization_name?: string | null;
  department_id?: string | null;
  department_name?: string | null;
  level?: string | null;
  match_type?: string | null;
  confidence?: number | null;
};

type RematchItem = {
  job_id?: string | null;
  filename?: string | null;
  action?: string | null;
  current?: MatchOrganization | null;
  suggested?: MatchOrganization | null;
  updated?: boolean | null;
};

type RematchSkippedItem = {
  job_id?: string | null;
  filename?: string | null;
  reason?: string | null;
  detail?: string | null;
  current?: MatchOrganization | null;
  suggested?: MatchOrganization | null;
};

type RematchFailedItem = {
  job_id?: string | null;
  status_code?: number | null;
  detail?: string | null;
};

export type BatchRematchPayload = {
  status?: string | null;
  dry_run?: boolean | null;
  include_manual?: boolean | null;
  minimum_confidence?: number | null;
  department_id?: string | null;
  department_name?: string | null;
  scanned_count?: number | null;
  candidate_count?: number | null;
  updated_count?: number | null;
  skipped_count?: number | null;
  failed_count?: number | null;
  matches?: RematchItem[] | null;
  skipped?: RematchSkippedItem[] | null;
  failed?: RematchFailedItem[] | null;
};

interface BatchRematchDialogProps {
  isOpen: boolean;
  preview: BatchRematchPayload | null;
  isLoading?: boolean;
  isApplying?: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}

function formatCount(value?: number | null) {
  return typeof value === "number" ? value.toLocaleString() : "--";
}

function confidenceText(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "未提供";
  }
  return `${Math.round(value * 100)}%`;
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
  tone: "slate" | "emerald" | "amber" | "rose";
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : tone === "amber"
        ? "border-amber-200 bg-amber-50 text-amber-800"
        : tone === "rose"
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : "border-slate-200 bg-slate-50 text-slate-800";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <div className="text-xs font-semibold uppercase tracking-[0.18em] opacity-70">{title}</div>
      <div className="mt-2 text-3xl font-bold tabular-nums">{formatCount(value)}</div>
      <div className="mt-1 text-sm opacity-80">{description}</div>
    </div>
  );
}

function OrganizationChip({
  label,
  item,
  emptyText,
}: {
  label: string;
  item?: MatchOrganization | null;
  emptyText: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{label}</div>
      {item?.organization_id ? (
        <div className="mt-2 space-y-2">
          <div className="text-sm font-semibold text-slate-900">{item.organization_name || "未命名组织"}</div>
          <div className="flex flex-wrap gap-2 text-xs">
            {item.department_name ? (
              <span className="rounded-full bg-amber-50 px-2 py-1 font-medium text-amber-700">
                部门：{item.department_name}
              </span>
            ) : null}
            {item.match_type ? (
              <span className="rounded-full bg-slate-100 px-2 py-1 font-medium text-slate-700">
                {item.match_type === "manual" ? "手动关联" : "自动匹配"}
              </span>
            ) : null}
            {typeof item.confidence === "number" ? (
              <span className="rounded-full bg-sky-50 px-2 py-1 font-medium text-sky-700">
                置信度 {confidenceText(item.confidence)}
              </span>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="mt-2 text-sm text-slate-500">{emptyText}</div>
      )}
    </div>
  );
}

function getActionLabel(action?: string | null) {
  return action === "reassociate" ? "改关联" : "新关联";
}

function getReasonTone(reason?: string | null) {
  switch ((reason || "").trim()) {
    case "manual_locked":
      return "amber";
    case "same_match":
      return "emerald";
    default:
      return "slate";
  }
}

export default function BatchRematchDialog({
  isOpen,
  preview,
  isLoading = false,
  isApplying = false,
  onClose,
  onConfirm,
}: BatchRematchDialogProps) {
  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isApplying) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isApplying, isOpen, onClose]);

  if (!isOpen) {
    return null;
  }

  const matches = preview?.matches || [];
  const skipped = preview?.skipped || [];
  const failed = preview?.failed || [];
  const isPreviewMode = preview?.status !== "applied";
  const scopeLabel = preview?.department_name
    ? `当前范围：${preview.department_name}`
    : "当前范围：全区";

  return (
    <div className="fixed inset-0 z-[170] flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-[28px] border border-white/60 bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-indigo-500">组织重匹配</div>
            <h2 className="mt-1 text-2xl font-bold text-slate-900">
              {isPreviewMode ? "批量重匹配预览" : "批量重匹配结果"}
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              默认只调整“自动匹配 / 未关联”的任务，不会直接覆盖手动关联结果。{scopeLabel}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isApplying}
            className="rounded-xl border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
          >
            关闭
          </button>
        </div>

        <div className="overflow-y-auto px-6 py-5">
          <div className="grid gap-3 md:grid-cols-4">
            <SectionCard title="扫描任务" value={preview?.scanned_count} description="已检查的历史任务数" tone="slate" />
            <SectionCard title="可调整" value={preview?.candidate_count} description="建议重匹配的任务数" tone="emerald" />
            <SectionCard title="已跳过" value={preview?.skipped_count} description="无须调整或不满足条件" tone="amber" />
            <SectionCard title="失败" value={preview?.failed_count} description="处理异常的任务数" tone="rose" />
          </div>

          <div className="mt-4 rounded-2xl border border-indigo-100 bg-indigo-50 px-4 py-3 text-sm text-indigo-800">
            匹配阈值：{confidenceText(preview?.minimum_confidence)}。建议先预览，再执行正式调整；全区范围任务较多时，生成预览会明显更慢。
          </div>

          {isLoading ? (
            <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center text-sm text-slate-500">
              正在生成重匹配预览，请稍候…
            </div>
          ) : null}

          {!isLoading && matches.length > 0 ? (
            <section className="mt-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-900">建议调整列表</h3>
                  <p className="mt-1 text-sm text-slate-500">每条都会展示当前关联和建议的新关联。</p>
                </div>
                {!isPreviewMode ? (
                  <div className="rounded-full bg-emerald-100 px-3 py-1 text-sm font-medium text-emerald-700">
                    已更新 {formatCount(preview?.updated_count)} 条
                  </div>
                ) : null}
              </div>
              <div className="mt-4 space-y-4">
                {matches.map((item, index) => (
                  <div key={`${item.job_id || "job"}-${index}`} className="rounded-3xl border border-slate-200 bg-slate-50/70 p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-slate-900">{item.filename || "未命名文件"}</div>
                        <div className="mt-1 text-xs text-slate-500">任务 ID：{item.job_id || "--"}</div>
                      </div>
                      <div className="rounded-full bg-indigo-100 px-3 py-1 text-sm font-medium text-indigo-700">
                        {getActionLabel(item.action)}
                      </div>
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      <OrganizationChip label="当前关联" item={item.current} emptyText="当前没有组织关联" />
                      <OrganizationChip label="建议关联" item={item.suggested} emptyText="暂无建议结果" />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {!isLoading && matches.length === 0 ? (
            <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center text-sm text-slate-500">
              当前没有需要批量重匹配的任务，可以直接关闭。
            </div>
          ) : null}

          {skipped.length > 0 ? (
            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-900">已跳过的任务</h3>
              <div className="mt-4 space-y-3">
                {skipped.map((item, index) => {
                  const tone = getReasonTone(item.reason);
                  const toneClass =
                    tone === "amber"
                      ? "border-amber-200 bg-amber-50"
                      : tone === "emerald"
                        ? "border-emerald-200 bg-emerald-50"
                        : "border-slate-200 bg-white";
                  return (
                    <div key={`${item.job_id || "skip"}-${index}`} className={`rounded-2xl border p-4 ${toneClass}`}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-slate-900">{item.filename || "未命名文件"}</div>
                          <div className="mt-1 text-xs text-slate-500">任务 ID：{item.job_id || "--"}</div>
                        </div>
                        <div className="rounded-full bg-white/80 px-3 py-1 text-xs font-medium text-slate-600">
                          {item.reason || "skipped"}
                        </div>
                      </div>
                      <div className="mt-2 text-sm text-slate-600">{item.detail || "已跳过"}</div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}

          {failed.length > 0 ? (
            <section className="mt-6">
              <h3 className="text-lg font-semibold text-slate-900">处理失败</h3>
              <div className="mt-4 space-y-3">
                {failed.map((item, index) => (
                  <div key={`${item.job_id || "failed"}-${index}`} className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-sm font-semibold text-rose-800">任务 ID：{item.job_id || "--"}</div>
                    <div className="mt-2 text-sm text-rose-700">
                      {item.status_code ? `HTTP ${item.status_code} · ` : ""}
                      {item.detail || "处理失败"}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-6 py-4">
          <div className="text-sm text-slate-500">
            {isPreviewMode
              ? "确认后会把候选任务改到建议的组织上，并刷新部门统计。"
              : "本次批量重匹配已完成，可回到部门树继续核对结果。"}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={isApplying}
              className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 transition hover:border-slate-300 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isPreviewMode ? "取消" : "关闭"}
            </button>
            {isPreviewMode ? (
              <button
                type="button"
                onClick={onConfirm}
                disabled={isApplying || matches.length === 0}
                className="rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300"
              >
                {isApplying ? "正在应用..." : "确认批量重匹配"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
