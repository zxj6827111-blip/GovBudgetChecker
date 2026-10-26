"use client";

import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from "lucide-react";

import type { AiScopeView } from "../../lib/aiScope";

const TONE_CLASSES: Record<AiScopeView["tone"], string> = {
  success: "border-success-200 bg-success-50 text-success-700",
  warning: "border-warning-200 bg-warning-50 text-warning-700",
  danger: "border-danger-200 bg-danger-50 text-danger-700",
  muted: "border-border bg-surface-100 text-slate-600",
};

const ICONS = {
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: ShieldAlert,
  muted: Info,
} as const;

export interface AiScopeBannerProps {
  scope: AiScopeView;
  testId: string;
  onRecheck?: () => void;
  recheckLabel?: string;
  recheckDisabled?: boolean;
  compact?: boolean;
}

/**
 * 检查范围条：任务页、复核台、材料详情共用的“已完成/未完成”口径。
 * 不用折叠隐藏；没有覆盖记录时明确写“未记录”，不显示成全部完成。
 */
export function AiScopeBanner({
  scope,
  testId,
  onRecheck,
  recheckLabel = "补检未完成项",
  recheckDisabled = false,
  compact = false,
}: AiScopeBannerProps) {
  const Icon = ICONS[scope.tone];
  return (
    <section
      data-testid={testId}
      data-ai-scope-tone={scope.tone}
      className={`rounded-md border px-3 ${compact ? "py-1.5" : "py-2.5"} text-xs leading-relaxed ${TONE_CLASSES[scope.tone]}`}
    >
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-semibold" data-testid={`${testId}-headline`}>
              {scope.headline}
            </span>
            <span className="opacity-80" data-testid={`${testId}-conclusion-scope`}>
              {scope.conclusionScopeLabel}
            </span>
          </div>
          {scope.summary ? (
            <p className="mt-1" data-testid={`${testId}-summary`}>
              {scope.summary}
            </p>
          ) : null}
          {scope.failedUnits.length > 0 ? (
            <ul className="mt-1 space-y-0.5" data-testid={`${testId}-failed-units`}>
              {scope.failedUnits.map((unit, index) => (
                <li key={`${unit.specialty}-${unit.pages.join("-")}-${index}`}>
                  未完成：{unit.specialtyLabel} · {unit.pagesLabel} · {unit.statusLabel}
                  {unit.errorType
                    ? `（${unit.errorType}${unit.httpStatus ? ` ${unit.httpStatus}` : ""}）`
                    : ""}
                  {unit.attempts && unit.attempts > 1 ? `，已尝试 ${unit.attempts} 次` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-1 opacity-80" data-testid={`${testId}-note`}>
            {scope.scopeLine}
          </p>
          {onRecheck ? (
            <button
              type="button"
              onClick={onRecheck}
              disabled={recheckDisabled}
              data-testid={`${testId}-recheck`}
              className="mt-1.5 rounded border border-current/30 bg-white/70 px-2 py-0.5 font-medium underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
            >
              {recheckLabel}
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
