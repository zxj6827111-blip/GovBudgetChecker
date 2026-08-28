/**
 * IssueCard：审核工作台「审核问题」tab 里的单张问题卡（Task 6.4）。
 *
 * 对照原型图：风险等级徽章、rule_id·规则版本、页码、标题、说明、证据引文（灰底
 * 引文块），三个操作按钮「确认问题 / 忽略 / 补充意见」。
 *
 * 颜色一律用语义令牌（primary/success/danger/warning/info/surface/brand/slate），
 * 不复用 app/lib/issueSeverity.ts 的 badgeClass/accentClass/panelClass——那批
 * className 用了 rose/red/amber/sky/violet 等被禁止的调色板（该文件是旧
 * task-review 组件在用的既有实现，本批约定生效前就存在，不属于本次改动范围，
 * 但新组件不应该继续引入这些禁止色，因此本卡片自己按 severity code 映射到
 * 语义 token，只复用 normalizeSeverityCode/getSeverityMeta(...).label/riskLabel
 * 这些不含颜色的纯语义部分）。
 *
 * evidence_status=degraded 的降级标识：这是 M2 evidence_guard 的成果第一次在
 * 前端露面，用户有权知道哪条证据不完整，因此降级问题卡必须带一个明显但不与
 * 严重度徽章混淆的独立标识（用 warning 语义色 + 说明文案，不是简单复用风险徽章）。
 */
"use client";

import { getSeverityMeta, normalizeSeverityCode, type SeverityCode } from "../../../lib/issueSeverity";
import type { Problem } from "../../../lib/mock";
import { isProblemDegraded } from "./reviewWorkbenchAdapters";

const SEVERITY_TONE_CLASSES: Record<SeverityCode, string> = {
  critical: "bg-danger-100 text-danger-700",
  high: "bg-danger-100 text-danger-700",
  medium: "bg-warning-100 text-warning-700",
  low: "bg-slate-100 text-slate-600",
  info: "bg-slate-100 text-slate-600",
  manual_review: "bg-info-100 text-info-700",
};

export type IssueWorkflowAction = "confirm" | "ignore" | "note";

export interface IssueCardProps {
  problem: Problem;
  isActive: boolean;
  workflowStatus: string | null;
  onSelect: () => void;
  onAction: (action: IssueWorkflowAction) => void;
  isSubmitting: boolean;
}

export function IssueCard({ problem, isActive, workflowStatus, onSelect, onAction, isSubmitting }: IssueCardProps) {
  const severityCode = normalizeSeverityCode(problem.severity);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const degraded = isProblemDegraded(problem);
  const isConfirmed = workflowStatus === "confirmed";
  const isIgnored = workflowStatus === "no_issue";

  return (
    <div
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          onSelect();
        }
      }}
      data-testid={`gbc-review-issue-card-${problem.id}`}
      data-workflow-status={workflowStatus ?? "pending"}
      className={`cursor-pointer rounded-md border p-3 transition-colors ${
        isActive ? "border-primary-500 bg-primary-50/40" : "border-border bg-white hover:border-primary-200"
      }`}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span className={`rounded-full px-2 py-0.5 font-medium ${SEVERITY_TONE_CLASSES[severityCode]}`}>
          {severityMeta.riskLabel}
        </span>
        <span className="font-mono text-slate-400">{problem.ruleId}</span>
        {problem.page ? <span className="text-slate-400">第 {problem.page} 页</span> : null}
        {isConfirmed ? <span className="rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已确认</span> : null}
        {isIgnored ? <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-500">已忽略</span> : null}
      </div>

      {degraded ? (
        <div
          className="mb-1.5 rounded-md bg-warning-50 px-2 py-1 text-[11px] font-medium text-warning-700"
          data-testid={`gbc-review-issue-degraded-badge-${problem.id}`}
        >
          证据不足待复核：本条为 AI 分析结果，因证据不完整已降级，未计入正式问题数
        </div>
      ) : null}

      <h4 className="mb-1 text-sm font-semibold text-slate-900">{problem.title}</h4>
      <p className="mb-2 text-xs leading-relaxed text-slate-600">{problem.description}</p>

      {problem.snippet ? (
        <div className="mb-2 rounded-md bg-surface-100 px-2.5 py-2 text-xs leading-relaxed text-slate-600">
          “{problem.snippet}”
        </div>
      ) : null}

      <div className="flex items-center gap-2" onClick={(event) => event.stopPropagation()}>
        <button
          type="button"
          onClick={() => onAction("confirm")}
          disabled={isSubmitting}
          data-testid={`gbc-review-issue-confirm-${problem.id}`}
          className="rounded-md border border-success-200 bg-success-100 px-2.5 py-1 text-xs font-medium text-success-700 transition-colors hover:bg-success-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          确认问题
        </button>
        <button
          type="button"
          onClick={() => onAction("ignore")}
          disabled={isSubmitting}
          data-testid={`gbc-review-issue-ignore-${problem.id}`}
          className="rounded-md border border-slate-200 bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          忽略
        </button>
        <button
          type="button"
          onClick={() => onAction("note")}
          disabled={isSubmitting}
          data-testid={`gbc-review-issue-note-${problem.id}`}
          className="rounded-md border border-border bg-white px-2.5 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          补充意见
        </button>
      </div>
    </div>
  );
}

export default IssueCard;
