/**
 * 「未能自动核对」条目需要文字依据时的填写面板，盖在解释区上（与忽略理由面板同一形态）。
 *
 * 服务端依据纪律不变（见 review_lifecycle_service._obligation_evidence_error）：
 * - 确认有问题：写明发现了什么问题，预填系统卡点原文，可改后提交；
 * - 不适用：选原因（“其他”须写说明），原因与说明拼成补核说明；局部确认
 *   草稿（单列空白确认为无此项）不能单独作为整条义务不适用的依据；
 * - 核对无误：条目有系统定位时一键提交，不经过本面板；没有定位时才要求
 *   写一句核对了哪一页、哪张表——须写明核对的关系/数字，局部取数确认
 *   （草稿）不构成关系核对结论。
 *
 * R2.2 附页 A3（轻路径）：面板展示该义务的全量阻断点与覆盖范围确认；
 * 局部“无此项/数值正确”只预填〔局部确认草稿〕，不自动选择或提交整项决定。
 */
"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { OBLIGATION_DECISION_LABELS } from "./obligationProblems";
import type { ObligationReviewItemRecord } from "./reviewLifecycleAdapters";

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
    hint: "选择本材料为什么不涉及这项检查，原因会随处理记录保存。局部“无此项”草稿不能单独作为整条义务不适用的依据。",
    placeholder: "补充说明（选“其他”时必填）",
  },
  verified_ok: {
    heading: "核对无误 · 写明核对结论",
    hint: "写明本次核对了哪些关系/哪些数字（如“合计=分项之和”）；系统定位只作证据位置自动带上，不构成核对结论。局部取数草稿不算关系核对结论。",
    placeholder: "例如：第 12 页基本支出决算表，人员经费 120.00 与公用经费 30.00 合计与说明 150.00 一致",
  },
};

export interface ObligationDecisionPanelProps {
  decision: ObligationPanelDecision;
  checkName: string;
  /** 确认有问题时预填的系统卡点（后端 detail）。 */
  defaultNote?: string | null;
  /** 义务条目（R2.2 A3：用于展示全量阻断点与覆盖范围确认）。 */
  item?: ObligationReviewItemRecord | null;
  isSubmitting: boolean;
  onSubmit: (note: string) => void;
  onCancel: () => void;
}

/** 阻断点分维计数（IR08）：缺数位置与结构卡点分开列出，不混为一个数。 */
function blockerCounts(item: ObligationReviewItemRecord | null | undefined): { missing: number; blocked: number } {
  return {
    missing: item?.missing_operands?.length ?? 0,
    blocked: item?.blocked_by?.length ?? 0,
  };
}

function describeOperands(item: ObligationReviewItemRecord | null | undefined): string[] {
  return (item?.missing_operands ?? []).map((operand) => {
    const page = typeof operand.page === "number" ? `第${operand.page}页` : "";
    const row = typeof operand.row_key === "string" && operand.row_key ? operand.row_key : "";
    const column = typeof operand.column === "string" && operand.column ? operand.column : "";
    const role = typeof operand.role === "string" && operand.role ? operand.role : "缺数";
    return [role, page, row, column].filter(Boolean).join(" · ");
  });
}

function describeBlocks(item: ObligationReviewItemRecord | null | undefined): string[] {
  return (item?.blocked_by ?? []).map(
    (block) => `结构卡点 · ${block.dimension} · ${block.reason_code}`);
}

export function ObligationDecisionPanel({
  decision,
  checkName,
  defaultNote,
  item,
  isSubmitting,
  onSubmit,
  onCancel,
}: ObligationDecisionPanelProps) {
  const copy = PANEL_COPY[decision];
  const [reasonCode, setReasonCode] = useState("");
  const [text, setText] = useState(decision === "verified_issue" ? (defaultNote ?? "").slice(0, NOTE_MAX_LENGTH) : "");
  const [scopeConfirmed, setScopeConfirmed] = useState(false);
  const reasonLabel = NOT_APPLICABLE_REASONS.find((option) => option.code === reasonCode)?.label ?? "";
  const note = decision === "not_applicable"
    ? reasonCode === "other" ? text.trim() : [reasonLabel, text.trim()].filter(Boolean).join("：")
    : text.trim();
  const complete = decision === "not_applicable"
    ? Boolean(reasonCode) && (reasonCode !== "other" || Boolean(text.trim()))
    : Boolean(text.trim());
  // R2.2 A3/IR04：所有关闭性决定（verified_ok/verified_issue/not_applicable）
  // 都须确认覆盖范围；pending 不经过本面板。
  const needsScope = true;
  const counts = blockerCounts(item);
  const operandLines = describeOperands(item);
  const blockLines = describeBlocks(item);
  const noDetail = counts.missing === 0 && counts.blocked === 0;

  const prefillLocalDraft = (kind: "none" | "correct") => {
    const operand = item?.missing_operands?.[0];
    const page = typeof operand?.page === "number" ? `第${operand.page}页` : "";
    const target = [page,
      typeof operand?.row_key === "string" && operand.row_key ? operand.row_key : "",
      typeof operand?.column === "string" && operand.column ? operand.column : "",
    ].filter(Boolean).join("") || "对应位置";
    const draft = kind === "none"
      ? `〔局部确认草稿〕${target}空白，本人确认为无此项`
      : `〔局部确认草稿〕${target}取数与原文一致`;
    setText((current) => {
      const base = current.trim();
      if (base.includes("〔局部确认草稿〕")) return base;
      return [draft, base].filter(Boolean).join("\n").slice(0, NOTE_MAX_LENGTH);
    });
  };

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

        {/* R2.2 A3/IR08：全量阻断清单（分维、不截断、可滚动）。 */}
        <section
          className="mb-3 rounded-md bg-surface-50 px-3 py-2.5"
          data-testid="gbc-review-obligation-blockers"
          data-missing-count={counts.missing}
          data-blocked-count={counts.blocked}
        >
          <h3 className="mb-1 text-xs font-semibold text-slate-600">
            {noDetail
              ? "阻断范围"
              : `阻断点清单（缺数位置 ${counts.missing} 项 · 结构卡点 ${counts.blocked} 项）`}
          </h3>
          {noDetail ? (
            <p className="text-xs leading-relaxed text-slate-600">
              该义务未登记关系级明细（可能为规则级阻断）：请对整条检查项核对，不能只按个别位置表态。
            </p>
          ) : (
            <ul className="grid max-h-48 gap-1 overflow-y-auto text-xs leading-relaxed text-slate-600">
              {operandLines.map((line, index) => (
                <li key={`m-${index}`}>{index + 1}. {line}</li>
              ))}
              {blockLines.map((line, index) => (
                <li key={`b-${index}`}>{line}</li>
              ))}
            </ul>
          )}
        </section>

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

        {decision === "not_applicable" ? (
          <button
            type="button"
            onClick={() => prefillLocalDraft("none")}
            data-testid="gbc-review-obligation-prefill-none"
            className="mb-2 rounded-md border border-border px-2.5 py-1.5 text-xs text-slate-600 hover:bg-surface-100"
          >从缺数位置预填“确认为无此项”（仅草稿，须另写整项依据）</button>
        ) : null}
        {decision === "verified_ok" ? (
          <button
            type="button"
            onClick={() => prefillLocalDraft("correct")}
            data-testid="gbc-review-obligation-prefill-correct"
            className="mb-2 rounded-md border border-border px-2.5 py-1.5 text-xs text-slate-600 hover:bg-surface-100"
          >从缺数位置预填“取数一致”（仅草稿，须另写关系核对结论）</button>
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

        {needsScope ? (
          <label className="mt-3 flex cursor-pointer items-start gap-2 text-xs leading-relaxed text-slate-700">
            <input
              type="checkbox"
              checked={scopeConfirmed}
              onChange={(event) => setScopeConfirmed(event.target.checked)}
              data-testid="gbc-review-obligation-scope-confirm"
              className="mt-0.5 accent-primary-600"
            />
            <span data-testid="gbc-review-obligation-scope-text">
              {noDetail
                ? "本人确认：本决定覆盖该义务全部阻断点（上方清单即完整集合）。范围确认不能代替适用性/核对依据。"
                : `本人确认：本决定覆盖该义务全部阻断点（缺数位置 ${counts.missing} 项、结构卡点 ${counts.blocked} 项，上方清单即完整集合）。范围确认不能代替适用性/核对依据。`}
            </span>
          </label>
        ) : null}
      </div>
      <div className="flex shrink-0 justify-end gap-2 border-t border-border px-4 py-2.5">
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100">取消</button>
        <button
          type="button"
          disabled={!complete || isSubmitting || (needsScope && !scopeConfirmed)}
          onClick={() => { if (complete && (!needsScope || scopeConfirmed)) onSubmit(note); }}
          data-testid="gbc-review-obligation-save"
          className="rounded-md border border-primary-600 bg-primary-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
        >{isSubmitting ? "保存中…" : OBLIGATION_DECISION_LABELS[decision]}</button>
      </div>
    </div>
  );
}

export default ObligationDecisionPanel;
