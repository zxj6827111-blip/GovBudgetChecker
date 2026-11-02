/**
 * 忽略问题前记录理由：盖在解释区上，选好理由才能确认忽略。
 * “其他”必须写说明；理由随工作流 note 保存（见 reviewDecisionNotes.ts）。
 */
"use client";

import { useState } from "react";
import { X } from "lucide-react";

import {
  IGNORE_DETAIL_MAX_LENGTH,
  IGNORE_REASON_OPTIONS,
  ignoreReasonLabel,
  isIgnoreReasonComplete,
  type IgnoreReason,
} from "./reviewDecisionNotes";

export interface IgnoreReasonPanelProps {
  problemTitle: string;
  isSubmitting: boolean;
  onSubmit: (reason: IgnoreReason) => void;
  onCancel: () => void;
}

export function IgnoreReasonPanel({ problemTitle, isSubmitting, onSubmit, onCancel }: IgnoreReasonPanelProps) {
  const [code, setCode] = useState("");
  const [detail, setDetail] = useState("");
  const complete = isIgnoreReasonComplete(code, detail);
  return (
    <div
      className="absolute inset-0 z-30 flex flex-col bg-white"
      data-testid="gbc-review-ignore-panel"
      onKeyDown={(event) => { if (event.key === "Escape") onCancel(); }}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
        <strong className="text-sm font-semibold text-slate-900">忽略问题 · 记录理由</strong>
        <button type="button" onClick={onCancel} aria-label="返回问题解释" className="rounded p-1 text-slate-500 hover:bg-surface-100">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 text-sm">
        <p className="mb-1 font-medium text-slate-800">{problemTitle}</p>
        <p className="mb-3 text-xs leading-relaxed text-slate-500">选择本次判断的依据，理由会随这条处理记录保存，并出现在导出的复核记录中。</p>
        <fieldset className="mb-4 grid grid-cols-2 gap-2" aria-label="忽略理由">
          {IGNORE_REASON_OPTIONS.map((option) => (
            <label
              key={option.code}
              className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 text-xs ${
                code === option.code ? "border-primary-500 bg-primary-50 text-primary-700" : "border-border text-slate-700 hover:bg-surface-50"
              }`}
            >
              <input
                type="radio"
                name="gbc-ignore-reason"
                value={option.code}
                checked={code === option.code}
                onChange={() => setCode(option.code)}
                data-testid={`gbc-review-ignore-reason-${option.code}`}
                className="accent-primary-600"
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <label htmlFor="gbc-ignore-detail" className="mb-1 block text-xs text-slate-600">补充说明{code === "other" ? "（必填）" : "（可选）"}</label>
        <textarea
          id="gbc-ignore-detail"
          value={detail}
          maxLength={IGNORE_DETAIL_MAX_LENGTH}
          onChange={(event) => setDetail(event.target.value)}
          rows={4}
          placeholder="例如核对的页码、口径依据或已披露的位置"
          data-testid="gbc-review-ignore-detail"
          className="w-full rounded-md border border-border px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        {code === "other" && !detail.trim() ? <p className="mt-1 text-xs text-warning-700">选择“其他”时须填写说明。</p> : null}
      </div>
      <div className="flex shrink-0 justify-end gap-2 border-t border-border px-4 py-2.5">
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100">取消</button>
        <button
          type="button"
          disabled={!complete || isSubmitting}
          onClick={() => {
            const label = ignoreReasonLabel(code);
            if (label && complete) onSubmit({ label, detail: detail.trim() });
          }}
          data-testid="gbc-review-ignore-save"
          className="rounded-md border border-primary-600 bg-primary-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
        >{isSubmitting ? "保存中…" : "确认忽略"}</button>
      </div>
    </div>
  );
}

export default IgnoreReasonPanel;
