/**
 * IssueNoteDialog：审核问题卡「补充意见」的备注输入框（Task 6.6）。
 *
 * 复用现有备注能力：src/services/issue_workflow_store.py 的 update_issue()
 * 签名早已带 `note: Optional[str]` 参数，_normalize_state 也已解析持久化该字段
 * ——本组件只是把这个已有能力接到 UI 上，未扩展后端、无需迁移兼容处理。
 */
"use client";

import { useState } from "react";

import { Button } from "../ui";

export interface IssueNoteDialogProps {
  problemTitle: string;
  initialNote: string;
  onSave: (note: string) => void;
  onCancel: () => void;
  isSubmitting: boolean;
}

export function IssueNoteDialog({ problemTitle, initialNote, onSave, onCancel, isSubmitting }: IssueNoteDialogProps) {
  const [note, setNote] = useState(initialNote);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      data-testid="gbc-review-issue-note-dialog"
    >
      <div className="w-full max-w-md rounded-md bg-white p-4 shadow-lg">
        <div className="mb-1 text-sm font-semibold text-slate-900">补充意见</div>
        <div className="mb-3 truncate text-xs text-slate-500">{problemTitle}</div>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={4}
          placeholder="填写复核意见，将随问题一并保存"
          data-testid="gbc-review-issue-note-textarea"
          className="w-full rounded-md border border-border px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel} data-testid="gbc-review-issue-note-cancel">
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => onSave(note)}
            disabled={isSubmitting}
            data-testid="gbc-review-issue-note-save"
          >
            {isSubmitting ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default IssueNoteDialog;
