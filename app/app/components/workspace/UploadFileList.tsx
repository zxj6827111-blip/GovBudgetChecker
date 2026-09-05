/**
 * UploadFileList：上传中心待上传文件列表（Task 5.1；前置修复 1 新增"补齐"入口）。
 *
 * 列：文件名、大小（真实 File.size）、页数（真实 preflight page_count，未知显示 —）、
 * 校验结论徽章（校验通过 / 需要确认 / 校验失败）。
 *
 * 校验状态来自 derivePreflightStatus()，该函数只依据 preflight 响应的真实字段
 * 判定，不按文件名猜测、不随机——"需要确认"必须对应年度/类型未识别到或
 * 组织匹配置信度不足（后端 current=null，即 confidence < 0.6）。
 *
 * 前置修复 1：needs_confirmation 状态的文件会额外显示"补齐"按钮，点击后
 * 展开 UploadConfirmationPanel 单文件编辑表单——这是"分析前确认闸门"必须提供的
 * 解决路径的前端入口，具体的状态解除逻辑在 UploadCenterPage 里完成。
 */
"use client";

import { useState } from "react";

import { Badge, Button } from "@/components/ui";
import type { BadgeTone } from "@/components/ui";

import {
  formatFileSizeMb,
  formatPageCountText,
  listPreflightConfirmationReasons,
  PREFLIGHT_STATUS_LABELS,
  type ManualConfirmationOverride,
  type PreflightResponseLike,
  type PreflightStatus,
} from "./uploadCenterAdapters";
import { UploadConfirmationPanel } from "./UploadConfirmationPanel";

export interface UploadFileEntry {
  id: string;
  file: File;
  status: "pending_preflight" | PreflightStatus;
  pageCount: number | null;
  errorMessage?: string;
  /** 该文件最新的 preflight 响应（含人工补齐覆盖后的有效值），用于计算缺失原因。 */
  preflight?: PreflightResponseLike | null;
}

function resolveStatusBadge(entry: UploadFileEntry): { tone: BadgeTone; label: string } {
  if (entry.status === "pending_preflight") {
    return { tone: "processing", label: "校验中…" };
  }
  if (entry.status === "passed") {
    return { tone: "done", label: PREFLIGHT_STATUS_LABELS.passed };
  }
  if (entry.status === "needs_confirmation") {
    return { tone: "lowconf", label: PREFLIGHT_STATUS_LABELS.needs_confirmation };
  }
  return { tone: "failed", label: PREFLIGHT_STATUS_LABELS.failed };
}

export interface UploadFileListProps {
  entries: UploadFileEntry[];
  onRemove: (id: string) => void;
  /** 前置修复 1：单文件补齐保存回调，父组件负责重新计算状态与提交时的真实取值。 */
  onManualConfirm?: (id: string, override: ManualConfirmationOverride) => void;
}

export function UploadFileList({ entries, onRemove, onManualConfirm }: UploadFileListProps) {
  const [expandedEntryId, setExpandedEntryId] = useState<string | null>(null);

  if (entries.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-slate-400" data-testid="gbc-upload-file-list-empty">
        暂无待上传文件，校验异常不会被静默忽略。
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border" data-testid="gbc-upload-file-list">
      {entries.map((entry) => {
        const badge = resolveStatusBadge(entry);
        const confirmationReasons = listPreflightConfirmationReasons(entry.preflight);
        const isExpanded = expandedEntryId === entry.id;
        return (
          <li key={entry.id} className="py-3" data-testid={`gbc-upload-file-row-${entry.id}`}>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-slate-900" title={entry.file.name}>
                  {entry.file.name}
                </div>
                <div className="mt-0.5 text-xs text-slate-400">
                  {formatFileSizeMb(entry.file.size)} · {formatPageCountText(entry.pageCount)}
                  {entry.errorMessage ? ` · ${entry.errorMessage}` : ""}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone={badge.tone}>{badge.label}</Badge>
                {entry.status === "needs_confirmation" && onManualConfirm ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setExpandedEntryId(isExpanded ? null : entry.id)}
                    data-testid={`gbc-upload-file-confirm-toggle-${entry.id}`}
                  >
                    补齐
                  </Button>
                ) : null}
                <button
                  type="button"
                  onClick={() => onRemove(entry.id)}
                  aria-label={`移除 ${entry.file.name}`}
                  data-testid={`gbc-upload-file-remove-${entry.id}`}
                  className="rounded-md px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                >
                  移除
                </button>
              </div>
            </div>

            {isExpanded && onManualConfirm ? (
              <UploadConfirmationPanel
                fileName={entry.file.name}
                reasons={confirmationReasons}
                onSave={(override) => {
                  onManualConfirm(entry.id, override);
                  setExpandedEntryId(null);
                }}
                onCancel={() => setExpandedEntryId(null)}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export default UploadFileList;
