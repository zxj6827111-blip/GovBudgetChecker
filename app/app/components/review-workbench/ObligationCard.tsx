/**
 * ObligationCard：「未能自动核对」条目的解释区（补核义务并入必须处理后的卡片）。
 *
 * 按“系统没能核对什么 → 为什么没核对成 → 请你核对什么、怎么判断 → 原文位置 →
 * 处理记录”的顺序阅读。标题是检查项名称，不是错误内容；分类（疑似材料问题 /
 * 疑似取数限制 / 需人工判断）只是提示，结论仍由下方三个按钮人工写入。
 * 字号随外层容器 font-size 等比缩放，这里一律用 em，与 IssueCard 一致。
 */
"use client";

import type { Problem } from "../../../lib/mock";
import { WorkflowStatusBadge } from "./IssueCard";
import { obligationGuidance, type ObligationGuidanceTone } from "./obligationGuidance";
import { OBLIGATION_DECISION_LABELS, readableObligationReason } from "./obligationProblems";
import type { ObligationReviewItemRecord } from "./reviewLifecycleAdapters";

const SECTION_TITLE = "mb-2 text-[0.8em] font-semibold text-slate-500";

const CATEGORY_TONE_CLASSES: Record<ObligationGuidanceTone, string> = {
  review: "bg-warning-100 text-warning-700",
  processing: "bg-info-100 text-info-700",
  neutral: "bg-slate-100 text-slate-600",
};

/** 文种口径冲突的补核要先确认材料口径（单位/部门），确认入口在「更多 → 材料信息」。 */
const SCOPE_OBLIGATION_ID = "OBL-STRUCT-TERMINOLOGY";

export interface ObligationCardProps {
  problem: Problem;
  item: ObligationReviewItemRecord;
  allItems: ObligationReviewItemRecord[];
  workflowStatus: string | null;
  selectedEvidenceRefKey?: string | null;
  onSelectEvidenceRef?: (refKey: string) => void;
  onOpenScope?: () => void;
}

export function ObligationCard({
  problem,
  item,
  allItems,
  workflowStatus,
  selectedEvidenceRefKey,
  onSelectEvidenceRef,
  onOpenScope,
}: ObligationCardProps) {
  const guidance = obligationGuidance(item, allItems);
  const checkName = item.title?.trim() || item.obligation_id;
  const reasonLabel = item.reason_label || item.reason || "";
  const reasonDetail = readableObligationReason(item.detail);
  const decisionLabel = item.decision && item.decision in OBLIGATION_DECISION_LABELS
    ? OBLIGATION_DECISION_LABELS[item.decision as keyof typeof OBLIGATION_DECISION_LABELS]
    : null;
  const refs = problem.evidenceRefs ?? [];

  return (
    <article
      data-testid="gbc-review-issue-detail"
      data-detail-problem-id={problem.id}
      data-workflow-status={workflowStatus ?? "pending"}
      className="px-4 py-4 text-slate-800 sm:px-5"
    >
      <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.75em] text-slate-500">
        <span className="rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700">未能自动核对</span>
        <span className={`rounded-full px-2 py-0.5 font-medium ${CATEGORY_TONE_CLASSES[guidance.categoryTone]}`}>{guidance.categoryLabel}</span>
        <span>{[item.group_title, item.obligation_id, problem.page ? `第 ${problem.page} 页` : ""].filter(Boolean).join(" · ")}</span>
        <WorkflowStatusBadge workflowStatus={workflowStatus} />
      </div>

      <h2 className="mb-3 break-words text-[1.25em] font-semibold leading-snug text-slate-900">{checkName}</h2>

      <section className="mb-4 rounded-md bg-warning-50 px-3.5 py-3" data-testid={`gbc-review-obligation-reason-${item.obligation_id}`}>
        <h3 className="mb-1 text-[0.8em] font-semibold text-warning-700">为什么没能自动核对</h3>
        <p className="whitespace-pre-line break-words text-[0.9em] leading-relaxed text-slate-700">
          {reasonLabel ? <span className="font-medium">{reasonLabel}</span> : null}
          {reasonLabel && reasonDetail ? "：" : ""}
          {reasonDetail || (reasonLabel ? "" : "系统没能自动完成这项检查。")}
        </p>
      </section>

      {/* R2.2 附页 A3/IR08：全量阻断清单——缺数位置与结构卡点分维列出、
          不截断（可滚动核对完整集合）；两个数组都空时只能说"未登记关系级
          明细"，不能凭空数组断言规则零回执。 */}
      {(() => {
        const missing = item.missing_operands ?? [];
        const blocked = item.blocked_by ?? [];
        const noDetail = missing.length === 0 && blocked.length === 0;
        return (
          <section
            className="mb-4 rounded-md bg-surface-50 px-3.5 py-3"
            data-testid={`gbc-obligation-blockers-${item.obligation_id}`}
            data-missing-count={missing.length}
            data-blocked-count={blocked.length}
          >
            <h3 className={SECTION_TITLE}>
              {noDetail ? "阻断范围" : `阻断点清单（缺数位置 ${missing.length} 项 · 结构卡点 ${blocked.length} 项）`}
            </h3>
            {noDetail ? (
              <p className="text-[0.85em] leading-relaxed text-slate-700">
                该义务未登记关系级明细（可能为规则级阻断）：人工表态须覆盖整条检查项。
              </p>
            ) : (
              <ul className="grid max-h-48 gap-1 overflow-y-auto text-[0.85em] leading-relaxed text-slate-700">
                {missing.map((operand, index) => {
                  const page = typeof operand.page === "number" ? `第${operand.page}页` : "";
                  const row = typeof operand.row_key === "string" && operand.row_key ? operand.row_key : "";
                  const column = typeof operand.column === "string" && operand.column ? operand.column : "";
                  const role = typeof operand.role === "string" && operand.role ? operand.role : "缺数";
                  return <li key={`m-${index}`}>{[role, page, row, column].filter(Boolean).join(" · ")}</li>;
                })}
                {blocked.map((block, index) => (
                  <li key={`b-${index}`}>结构卡点 · {block.dimension} · {block.reason_code}</li>
                ))}
              </ul>
            )}
          </section>
        );
      })()}

      <section className="mb-4" data-testid={`gbc-obligation-guidance-${item.obligation_id}`}>
        <h3 className={SECTION_TITLE}>请你核对</h3>
        <p className="mb-2 text-[0.9em] leading-relaxed text-slate-700">{guidance.whatToConfirm}</p>
        <h3 className={SECTION_TITLE}>怎么判断</h3>
        <p className="text-[0.9em] leading-relaxed text-slate-700">{guidance.howToDecide}</p>
        {guidance.sameSource ? (
          <p className="mt-2 text-[0.8em] leading-relaxed text-warning-700" data-testid={`gbc-obligation-same-source-${item.obligation_id}`}>
            同源：{[...guidance.sameSource.obligationIds, ...guidance.sameSource.findingIds].join(" / ")}
            {guidance.sameSource.note ? ` — ${guidance.sameSource.note}` : ""}
          </p>
        ) : null}
        {item.obligation_id === SCOPE_OBLIGATION_ID && onOpenScope ? (
          <button type="button" onClick={onOpenScope} className="mt-2 text-[0.85em] font-medium text-primary-700 hover:underline" data-testid="gbc-obligation-open-scope">
            先确认材料口径（单位/部门）→
          </button>
        ) : null}
      </section>

      <section className="mb-4">
        <h3 className={SECTION_TITLE}>原文位置</h3>
        {refs.length > 0 ? (
          <div className="grid gap-1.5">
            {refs.map((ref, index) => (
              <button
                key={ref.key}
                type="button"
                onClick={() => onSelectEvidenceRef?.(ref.key)}
                aria-pressed={selectedEvidenceRefKey === ref.key}
                data-testid={`gbc-review-evidence-ref-${problem.id}-${ref.key}`}
                className={`grid w-full grid-cols-[1.6em_minmax(0,1fr)] items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${
                  selectedEvidenceRefKey === ref.key ? "border-primary-500 bg-primary-50" : "border-border hover:bg-surface-50"
                }`}
                title={`定位到原文：${ref.locationText}`}
              >
                <span className="mt-0.5 grid h-[1.6em] w-[1.6em] place-items-center rounded bg-surface-100 text-[0.75em] text-slate-500">{String.fromCharCode(65 + (index % 26))}</span>
                <span className="min-w-0">
                  <span className="block break-words text-[0.88em] leading-relaxed text-slate-800">{ref.locationText}</span>
                  <span className="block text-[0.72em] text-slate-500">{ref.bbox ? "定位框（表级/证据框）" : "整页"} ↖</span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[0.85em] leading-relaxed text-slate-500">系统没有定位到具体页码，请按检查项名称在原文中查找。</p>
        )}
      </section>

      {guidance.requirement ? (
        <details className="mb-4 text-[0.8em] text-slate-500">
          <summary className="cursor-pointer">检查依据</summary>
          <p className="mt-1 leading-relaxed">{guidance.requirement}</p>
        </details>
      ) : null}

      {item.decided_by || item.decided_at ? (
        <section className="mb-4 border-t border-border pt-3" data-testid={`gbc-obligation-record-${item.obligation_id}`}>
          <h3 className={SECTION_TITLE}>处理记录</h3>
          <p className="whitespace-pre-line break-words text-[0.85em] leading-relaxed text-slate-700">
            {[decisionLabel, item.decided_by, item.decided_at].filter(Boolean).join(" · ")}
            {item.note ? `\n说明：${item.note}` : ""}
            {item.evidence_reference ? `\n依据：${item.evidence_reference}` : ""}
          </p>
        </section>
      ) : null}
    </article>
  );
}

export default ObligationCard;
