"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Info, ShieldAlert } from "lucide-react";

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

const ACTION_BUTTON =
  "whitespace-nowrap rounded border border-current/30 bg-white/70 px-2 py-0.5 font-medium underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:opacity-60";

export interface AiScopeBannerProps {
  scope: AiScopeView;
  testId: string;
  onRecheck?: () => void;
  recheckLabel?: string;
  /** 按钮文字缩短时的完整说法（悬停提示）。 */
  recheckTitle?: string;
  recheckDisabled?: boolean;
  compact?: boolean;
  /**
   * full：完整说明（状态面板、材料详情）；
   * inline：一行状态条（问题队列、任务页顶部），技术原因放在“详情”里。
   */
  variant?: "full" | "inline";
  /** inline 下点“详情”交给外部（如打开状态面板）；不传则就地展开。 */
  onShowDetails?: () => void;
}

function failedUnitLine(unit: AiScopeView["failedUnits"][number]): string {
  return `未完成：${unit.specialtyLabel} · ${unit.pagesLabel} · ${unit.statusLabel}`
    + (unit.errorType ? `（${unit.errorType}${unit.httpStatus ? ` ${unit.httpStatus}` : ""}）` : "")
    + (unit.attempts && unit.attempts > 1 ? `，已尝试 ${unit.attempts} 次` : "");
}

/** 一行状态条的正文：完成计数 · 未完成专项与页码（或一句后果）· 结论作用域。 */
export function aiScopeInlineParts(scope: AiScopeView): { groups: string; brief: string; conclusion: string } {
  return {
    groups: scope.failedGroups.length > 0
      ? `未完成：${scope.failedGroups.map((group) => `${group.specialtyLabel}${group.pagesLabel}`).join("；")}`
      : "",
    brief: scope.brief,
    conclusion: scope.conclusionScopeShort,
  };
}

function ScopeDetails({ scope, testId }: { scope: AiScopeView; testId: string }) {
  return (
    <>
      {scope.summary ? (
        <p className="mt-1" data-testid={`${testId}-summary`}>
          {scope.summary}
        </p>
      ) : null}
      {scope.failedUnits.length > 0 ? (
        <ul className="mt-1 space-y-0.5" data-testid={`${testId}-failed-units`}>
          {scope.failedUnits.map((unit, index) => (
            <li key={`${unit.specialty}-${unit.pages.join("-")}-${index}`}>{failedUnitLine(unit)}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-1 opacity-80" data-testid={`${testId}-note`}>
        {scope.scopeLine}
      </p>
    </>
  );
}

/**
 * 检查范围条：任务页、复核台、材料详情共用的“已完成/未完成”口径。
 * 完成计数、未完成专项与页码、结论作用域始终直接可见；inline 只把错误类型、
 * 尝试次数等排查信息收进“详情”。没有覆盖记录时明确写“未记录”，不显示成全部完成。
 */
export function AiScopeBanner({
  scope,
  testId,
  onRecheck,
  recheckLabel = "补检未完成项",
  recheckTitle,
  recheckDisabled = false,
  compact = false,
  variant = "full",
  onShowDetails,
}: AiScopeBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const Icon = ICONS[scope.tone];
  const recheckButton = onRecheck ? (
    <button
      type="button"
      onClick={onRecheck}
      disabled={recheckDisabled}
      title={recheckTitle}
      data-testid={`${testId}-recheck`}
      className={ACTION_BUTTON}
    >
      {recheckLabel}
    </button>
  ) : null;

  if (variant === "inline") {
    const parts = aiScopeInlineParts(scope);
    const fullText = [
      `${scope.headline}（${scope.conclusionScopeLabel}）`,
      scope.summary,
      ...scope.failedUnits.map(failedUnitLine),
      scope.scopeLine,
    ].filter(Boolean).join("\n");
    return (
      <section
        data-testid={testId}
        data-ai-scope-tone={scope.tone}
        data-ai-scope-variant="inline"
        className={`rounded-md border px-2.5 py-1 text-xs leading-relaxed ${TONE_CLASSES[scope.tone]}`}
      >
        <div className="flex items-start gap-2">
          <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <p className="line-clamp-2 min-w-0 flex-1" title={fullText} data-testid={`${testId}-line`}>
            <span className="font-semibold" data-testid={`${testId}-headline`}>{scope.headline}</span>
            {parts.groups ? <span data-testid={`${testId}-failed-groups`}> · {parts.groups}</span> : null}
            {parts.brief ? <span data-testid={`${testId}-brief`}> · {parts.brief}</span> : null}
            {parts.conclusion ? (
              <span className="opacity-80" data-testid={`${testId}-conclusion-scope`}> · {parts.conclusion}</span>
            ) : null}
          </p>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={onShowDetails ?? (() => setExpanded((value) => !value))}
              aria-expanded={onShowDetails ? undefined : expanded}
              data-testid={`${testId}-details-toggle`}
              className="inline-flex items-center gap-0.5 whitespace-nowrap rounded px-1 py-0.5 font-medium underline-offset-2 hover:underline"
            >
              详情
              {onShowDetails ? null : (
                <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
              )}
            </button>
            {recheckButton}
          </div>
        </div>
        {expanded && !onShowDetails ? (
          <div className="ml-[22px] mt-1 border-t border-current/20 pb-0.5 pt-0.5" data-testid={`${testId}-details`}>
            <ScopeDetails scope={scope} testId={testId} />
          </div>
        ) : null}
      </section>
    );
  }

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
          <ScopeDetails scope={scope} testId={testId} />
          {recheckButton ? <div className="mt-1.5">{recheckButton}</div> : null}
        </div>
      </div>
    </section>
  );
}
