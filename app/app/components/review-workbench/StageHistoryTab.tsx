/**
 * StageHistoryTab：右栏 tab 3「阶段记录」（Task 6.4，接第一批 Task 3 的阶段数据）。
 *
 * 5 态阶段（上传/PDF 解析/元数据识别/规则与 AI 分析/质量门禁），不含 OCR。
 * 失败任务显示 stage_failed_at；进度未知显示"—"（复用 isProgressUnknown/
 * formatProgressText，不重新判定 null——与 StageProgress 组件同一套判定逻辑）。
 */
"use client";

import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";

import { formatProgressText, isProgressUnknown } from "../ui/stageProgressStyles";
import { deriveStageHistory, type StageHistoryEntry, type StageHistoryStatus } from "./reviewWorkbenchAdapters";

export interface StageHistoryTabProps {
  stageProgress: { phase?: string | null; percent?: number | null } | null | undefined;
  stageFailedAt: { phase?: string | null } | null | undefined;
}

function StageIcon({ status }: { status: StageHistoryStatus }) {
  if (status === "done") {
    return <CheckCircle2 className="h-4 w-4 text-success-600" aria-hidden="true" />;
  }
  if (status === "current") {
    return <Loader2 className="h-4 w-4 animate-spin text-primary-600" aria-hidden="true" />;
  }
  if (status === "failed") {
    return <XCircle className="h-4 w-4 text-danger-600" aria-hidden="true" />;
  }
  return <CircleDashed className="h-4 w-4 text-slate-300" aria-hidden="true" />;
}

function statusLabel(entry: StageHistoryEntry): string {
  if (entry.status === "current") {
    return isProgressUnknown(entry.percent) ? "进行中 · —" : `进行中 · ${formatProgressText(entry.percent)}`;
  }
  if (entry.status === "failed") {
    return "失败";
  }
  if (entry.status === "done") {
    return "已完成";
  }
  if (entry.status === "unknown") {
    return "—";
  }
  return "未开始";
}

export function StageHistoryTab({ stageProgress, stageFailedAt }: StageHistoryTabProps) {
  const entries = deriveStageHistory(stageProgress, stageFailedAt);

  return (
    <div className="h-full overflow-y-auto px-4 py-3" data-testid="gbc-review-stage-history-tab">
      <ul className="space-y-4">
        {entries.map((entry, index) => (
          <li key={entry.stage} className="relative flex items-start gap-3" data-testid={`gbc-review-stage-${entry.stage}`}>
            {index !== entries.length - 1 ? (
              <span
                className={`absolute left-2 top-6 h-full w-px ${
                  entry.status === "done" ? "bg-success-300" : "bg-border"
                }`}
                aria-hidden="true"
              />
            ) : null}
            <span className="relative z-10 flex h-4 w-4 items-center justify-center bg-surface-50">
              <StageIcon status={entry.status} />
            </span>
            <div>
              <div
                className={`text-sm font-medium ${
                  entry.status === "current"
                    ? "text-primary-700"
                    : entry.status === "failed"
                      ? "text-danger-700"
                      : "text-slate-900"
                }`}
              >
                {entry.label}
              </div>
              <div className="text-xs text-slate-400" data-testid={`gbc-review-stage-status-${entry.stage}`}>
                {statusLabel(entry)}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default StageHistoryTab;
