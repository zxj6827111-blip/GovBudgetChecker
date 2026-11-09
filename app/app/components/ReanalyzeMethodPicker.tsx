"use client";

import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import {
  DEFAULT_REANALYZE_METHOD,
  canSubmitReanalyze,
  inheritBlockedReason,
  lastExecutionHint,
  lastRunSummary,
  type ReanalyzeCapabilities,
  type ReanalyzeMethodValue,
  type ReanalyzeOptionsPreview,
} from "@/components/reanalyzeOptions";

export {
  DEFAULT_REANALYZE_METHOD,
  buildReanalyzeRequestBody,
  canSubmitReanalyze,
  describeCheckMethod,
} from "@/components/reanalyzeOptions";
export type { ReanalyzeMethodValue } from "@/components/reanalyzeOptions";

type ReanalyzeMethodPickerProps = {
  /** 单条：加载该材料的上轮配置预览。批量不传。 */
  jobId?: string;
  batch?: boolean;
  value: ReanalyzeMethodValue;
  onChange: (value: ReanalyzeMethodValue) => void;
  disabled?: boolean;
  className?: string;
  testId?: string;
  /** 向父组件上报已加载的预览/能力（对话框用于提交门禁，避免二次请求）。 */
  onStateChange?: (state: { preview: ReanalyzeOptionsPreview | null; capabilities: ReanalyzeCapabilities | null }) => void;
};

/** 统一的重新分析检查方式选择：按上次方式（默认）/ 使用大模型参与（可选本次模型）。 */
export default function ReanalyzeMethodPicker({
  jobId,
  batch = false,
  value,
  onChange,
  disabled = false,
  className,
  testId = "reanalyze-method-picker",
  onStateChange,
}: ReanalyzeMethodPickerProps) {
  const [preview, setPreview] = useState<ReanalyzeOptionsPreview | null>(null);
  const [capabilities, setCapabilities] = useState<ReanalyzeCapabilities | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoadError(null);
    const load = async () => {
      try {
        if (jobId && !batch) {
          const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/reanalyze-options`, {
            cache: "no-store",
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = (await response.json()) as ReanalyzeOptionsPreview;
          if (!payload || typeof payload !== "object" || !payload.inherit || !payload.capabilities) {
            throw new Error("invalid preview payload");
          }
          if (cancelled) return;
          setPreview(payload);
          setCapabilities(payload.capabilities);
        } else {
          const response = await fetch("/api/ai/capabilities", { cache: "no-store", signal: controller.signal });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = (await response.json()) as ReanalyzeCapabilities;
          if (cancelled) return;
          setCapabilities(payload);
        }
      } catch {
        if (!cancelled) setLoadError("无法读取检查方式配置，请重试。");
      }
    };
    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId, batch, reloadKey]);

  const blockedReason = batch ? null : inheritBlockedReason(preview);
  const executionHint = batch ? null : lastExecutionHint(preview);
  const aiConfigured = Boolean(capabilities?.enabled);

  useEffect(() => {
    onStateChange?.({ preview, capabilities });
  }, [preview, capabilities, onStateChange]);

  // 继承不可用时保持默认选中并禁用提交，等用户主动选择第二项；不替用户改选大模型。

  const selectInherit = () => onChange({ ...value, checkMethod: "inherit" });
  const selectAi = () =>
    onChange({ checkMethod: "ai", aiProfileId: value.aiProfileId || capabilities?.active_profile_id || "" });

  return (
    <section className={cn("space-y-2", className)} data-testid={testId}>
      <label
        className={cn(
          "flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
          disabled || blockedReason ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        )}
      >
        <input
          type="radio"
          name={`${testId}-method`}
          checked={value.checkMethod === "inherit"}
          disabled={disabled || Boolean(blockedReason) || (!batch && !preview && !loadError)}
          onChange={selectInherit}
          data-testid={`${testId}-inherit`}
          className="mt-0.5 h-4 w-4 border-slate-300 text-primary-600 focus:ring-primary-500"
        />
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-800">
            {batch ? "按各材料上次检查方式重新分析（逐条继承）" : "按上次检查方式重新分析"}
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500" data-testid={`${testId}-inherit-summary`}>
            {batch
              ? "每条材料分别继承其最近一轮记录的本地规则、大模型开关与原模型；历史缺失或原模型不可用的条目会单独报错，不影响其他条目。"
              : loadError || lastRunSummary(preview)}
          </p>
          {executionHint ? (
            <p className="mt-1 text-xs leading-5 text-warning-700" data-testid={`${testId}-execution-hint`}>
              {executionHint}
            </p>
          ) : null}
          {preview?.model.state === "changed" && preview.model.message ? (
            <p className="mt-1 text-xs leading-5 text-warning-700" data-testid={`${testId}-model-warning`}>
              {preview.model.message}
            </p>
          ) : null}
          {blockedReason ? (
            <p className="mt-1 text-xs leading-5 text-danger-600" data-testid={`${testId}-inherit-blocked`}>
              {blockedReason}
            </p>
          ) : null}
        </div>
      </label>
      <label
        className={cn(
          "flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
          disabled || !aiConfigured ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        )}
      >
        <input
          type="radio"
          name={`${testId}-method`}
          checked={value.checkMethod === "ai"}
          disabled={disabled || !aiConfigured}
          onChange={selectAi}
          data-testid={`${testId}-ai`}
          className="mt-0.5 h-4 w-4 border-slate-300 text-primary-600 focus:ring-primary-500"
        />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-slate-800">使用大模型参与重新分析</div>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            本地规则 + 大模型语义复核，可选择本次使用的模型。
          </p>
          {value.checkMethod === "ai" && aiConfigured ? (
            <div className="mt-2">
              {capabilities?.profiles?.length ? (
                <select
                  aria-label="本次检查模型"
                  value={value.aiProfileId || capabilities.active_profile_id || ""}
                  disabled={disabled}
                  onChange={(event) => onChange({ checkMethod: "ai", aiProfileId: event.target.value })}
                  data-testid={`${testId}-model-select`}
                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700"
                >
                  {capabilities.profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name} · {profile.model}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="text-xs text-slate-500">本次模型：{capabilities?.model || "环境配置模型"}</span>
              )}
            </div>
          ) : null}
          {!aiConfigured && capabilities ? (
            <p className="mt-1 text-xs leading-5 text-slate-500">大模型检查未启用，管理员可在系统设置中配置。</p>
          ) : null}
        </div>
      </label>
      {loadError ? (
        <button
          type="button"
          disabled={disabled}
          onClick={() => setReloadKey((key) => key + 1)}
          className="text-xs text-primary-700 underline"
          data-testid={`${testId}-reload`}
        >
          刷新检查方式配置
        </button>
      ) : null}
    </section>
  );
}

type ReanalyzeMethodDialogProps = {
  jobId?: string;
  batch?: boolean;
  title?: string;
  confirmLabel?: string;
  submitting?: boolean;
  /** 入口有明确意图时预选（如"使用大模型重新分析"快捷入口）；缺省为按上次方式。 */
  initialValue?: ReanalyzeMethodValue;
  onConfirm: (value: ReanalyzeMethodValue) => void;
  onCancel: () => void;
};

/** 按钮入口共用的对话框：内嵌选择器，确认前禁用不可用提交。 */
export function ReanalyzeMethodDialog({
  jobId,
  batch = false,
  title = "重新分析",
  confirmLabel = "开始重新分析",
  submitting = false,
  initialValue = DEFAULT_REANALYZE_METHOD,
  onConfirm,
  onCancel,
}: ReanalyzeMethodDialogProps) {
  const [value, setValue] = useState<ReanalyzeMethodValue>(initialValue);
  const [preview, setPreview] = useState<ReanalyzeOptionsPreview | null>(null);
  const [capabilities, setCapabilities] = useState<ReanalyzeCapabilities | null>(null);
  const handleStateChange = useCallback(
    ({ preview: nextPreview, capabilities: nextCapabilities }: { preview: ReanalyzeOptionsPreview | null; capabilities: ReanalyzeCapabilities | null }) => {
      setPreview(nextPreview);
      setCapabilities(nextCapabilities);
    },
    [],
  );

  const submitDisabled = submitting || !canSubmitReanalyze(value, preview, capabilities, batch);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4" role="dialog" aria-modal="true" aria-label={title} data-testid="reanalyze-method-dialog">
      <div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          重新分析会生成新的分析版本；历史分析、原件、校正记录与复核记录都会保留。
        </p>
        <ReanalyzeMethodPicker
          jobId={jobId}
          batch={batch}
          value={value}
          onChange={setValue}
          disabled={submitting}
          className="mt-3"
          onStateChange={handleStateChange}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
            data-testid="reanalyze-method-cancel"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onConfirm(value)}
            disabled={submitDisabled}
            className="rounded-md bg-primary-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            data-testid="reanalyze-method-confirm"
          >
            {submitting ? "提交中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}