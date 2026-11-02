/**
 * 「未能自动核对」条目需要文字依据时的填写面板，盖在解释区上（与忽略理由面板同一形态）。
 *
 * 服务端依据纪律不变（见 review_lifecycle_service._obligation_evidence_error）：
 * - 确认有问题：写明发现了什么问题，预填系统卡点原文，可改后提交；
 * - 不适用：选原因（“其他”须写说明），原因与说明拼成补核说明；
 * - 核对无误：条目有系统定位时一键提交，不经过本面板；没有定位时才要求写一句核对了哪里。
 */
"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { OBLIGATION_DECISION_LABELS } from "./obligationProblems";

export type ObligationPanelDecision = "verified_ok" | "verified_issue" | "not_applicable";

const NOTE_MAX_LENGTH = 400;

const NOT_APPLICABLE_REASONS = [
  { code: "not_involved", label: "本材料不涉及此项" },
  { code: "empty_statement", label: "已有空表或无此项说明" },
  { code: "other", label: "其他" },
] as const;

const PANEL_COPY: Record<ObligationPanelDecision, { heading: string; hint: string; placeholder: string }> = {
  verified_issue: {
    heading: "确认有问题 · 写明问题",
    hint: "写明原文哪里有问题。已预填系统发现的情况，可直接修改。",
    placeholder: "例如：说明第 18 页写“其他城乡社区支出”，表内同编码科目为“城乡社区支出”",
  },
  not_applicable: {
    heading: "不适用 · 记录原因",
    hint: "选择本材料为什么不涉及这项检查，原因会随处理记录保存。",
    placeholder: "补充说明（选“其他”时必填）",
  },
  verified_ok: {
    heading: "核对无误 · 写明核对位置",
    hint: "系统没有定位到具体页码，请写一句核对了哪一页、哪张表。",
    placeholder: "例如：第 12 页基本支出决算表，人员经费与公用经费合计与说明一致",
  },
};

export interface ObligationDecisionPanelProps {
  decision: ObligationPanelDecision;
  checkName: string;
  /** 确认有问题时预填的系统卡点（后端 detail）。 */
  defaultNote?: string | null;
  isSubmitting: boolean;
  onSubmit: (note: string) => void;
  onCancel: () => void;
}

export function ObligationDecisionPanel({
  decision,
  checkName,
  defaultNote,
  isSubmitting,
  onSubmit,
  onCancel,
}: ObligationDecisionPanelProps) {
  const copy = PANEL_COPY[decision];
  const [reasonCode, setReasonCode] = useState("");
  const [text, setText] = useState(decision === "verified_issue" ? (defaultNote ?? "").slice(0, NOTE_MAX_LENGTH) : "");
  const reasonLabel = NOT_APPLICABLE_REASONS.find((option) => option.code === reasonCode)?.label ?? "";
  const note = decision === "not_applicable"
    ? reasonCode === "other" ? text.trim() : [reasonLabel, text.trim()].filter(Boolean).join("：")
    : text.trim();
  const complete = decision === "not_applicable"
    ? Boolean(reasonCode) && (reasonCode !== "other" || Boolean(text.trim()))
    : Boolean(text.trim());

  return (
    <div
      className="absolute inset-0 z-30 flex flex-col bg-white"
      data-testid="gbc-review-obligation-panel"
      data-obligation-decision={decision}
      onKeyDown={(event) => { if (event.key === "Escape") onCancel(); }}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2.5">
        <strong className="text-sm font-semibold text-slate-900">{copy.heading}</strong>
        <button type="button" onClick={onCancel} aria-label="返回条目解释" className="rounded p-1 text-slate-500 hover:bg-surface-100">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 text-sm">
        <p className="mb-1 font-medium text-slate-800">{checkName}</p>
        <p className="mb-3 text-xs leading-relaxed text-slate-500">{copy.hint}</p>
        {decision === "not_applicable" ? (
          <fieldset className="mb-4 grid gap-2" aria-label="不适用原因">
            {NOT_APPLICABLE_REASONS.map((option) => (
              <label
                key={option.code}
                className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 text-xs ${
                  reasonCode === option.code ? "border-primary-500 bg-primary-50 text-primary-700" : "border-border text-slate-700 hover:bg-surface-50"
                }`}
              >
                <input
                  type="radio"
                  name="gbc-obligation-not-applicable-reason"
                  value={option.code}
                  checked={reasonCode === option.code}
                  onChange={() => setReasonCode(option.code)}
                  data-testid={`gbc-review-obligation-reason-${option.code}`}
                  className="accent-primary-600"
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        ) : null}
        <textarea
          value={text}
          maxLength={NOTE_MAX_LENGTH}
          onChange={(event) => setText(event.target.value)}
          rows={4}
          placeholder={copy.placeholder}
          aria-label={copy.heading}
          data-testid="gbc-review-obligation-note"
          className="w-full rounded-md border border-border px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
      </div>
      <div className="flex shrink-0 justify-end gap-2 border-t border-border px-4 py-2.5">
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100">取消</button>
        <button
          type="button"
          disabled={!complete || isSubmitting}
          onClick={() => { if (complete) onSubmit(note); }}
          data-testid="gbc-review-obligation-save"
          className="rounded-md border border-primary-600 bg-primary-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
        >{isSubmitting ? "保存中…" : OBLIGATION_DECISION_LABELS[decision]}</button>
      </div>
    </div>
  );
}

export default ObligationDecisionPanel;
