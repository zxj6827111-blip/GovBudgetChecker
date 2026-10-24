/**
 * IssueCard：审核工作台「审核问题」tab 的问题详情，以及上方列表里的紧凑行（Task 6.4）。
 *
 * 问题区采用「上列表、下详情」：IssueRow 一行一条，只放风险、标题、来源、处理
 * 状态和页码，方便一眼看全；IssueCard 只展示当前选中的一条——风险等级徽章、
 * rule_id·规则版本、页码、标题、说明、证据引文（灰底引文块），以及固定在底部的
 * 操作按钮「确认问题 / 忽略 / 补充意见」，长说明滚动时按钮也不会被推出视野。
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

import { evidenceExcerptLabel } from "@/lib/evidenceIdentity";

import { getSeverityMeta, normalizeSeverityCode, type SeverityCode } from "../../../lib/issueSeverity";
import type { Problem } from "../../../lib/mock";
import { dedupeEvidenceRefsForDisplay, isProblemDegraded } from "./reviewWorkbenchAdapters";

const SEVERITY_TONE_CLASSES: Record<SeverityCode, string> = {
  critical: "bg-danger-100 text-danger-700",
  high: "bg-danger-100 text-danger-700",
  medium: "bg-warning-100 text-warning-700",
  low: "bg-slate-100 text-slate-600",
  info: "bg-slate-100 text-slate-600",
  manual_review: "bg-info-100 text-info-700",
};

export type IssueWorkflowAction = "confirm" | "ignore" | "note" | "restore";

function sourceLabel(problem: Problem): string {
  return (problem.findingSources ?? (problem.source === "ai" || problem.source === "rule" ? [problem.source] : []))
    .map((source) => source === "ai" ? "大模型" : "本地规则")
    .join(" + ");
}

function WorkflowStatusBadge({ workflowStatus }: { workflowStatus: string | null }) {
  if (workflowStatus === "confirmed") return <span className="shrink-0 rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已确认</span>;
  if (workflowStatus === "no_issue") return <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-500">已忽略</span>;
  if (workflowStatus === "in_package") return <span className="shrink-0 rounded-full bg-success-100 px-2 py-0.5 font-medium text-success-700">已进整改包</span>;
  return null;
}

export interface IssueRowProps {
  problem: Problem;
  isActive: boolean;
  workflowStatus: string | null;
  onSelect: () => void;
}

/** 列表行：沿用原问题卡的定位属性（data-review-problem-id / aria-current / 处理状态）。 */
export function IssueRow({ problem, isActive, workflowStatus, onSelect }: IssueRowProps) {
  const severityCode = normalizeSeverityCode(problem.severity);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const sources = sourceLabel(problem);
  return (
    <div
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      data-testid={`gbc-review-issue-card-${problem.id}`}
      data-review-problem-id={problem.id}
      data-workflow-status={workflowStatus ?? "pending"}
      aria-current={isActive ? "true" : undefined}
      title={problem.title}
      className={`flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
        isActive ? "border-primary-500 bg-primary-50" : "border-transparent hover:bg-surface-100"
      }`}
    >
      <span className={`shrink-0 rounded-full px-2 py-0.5 font-medium ${SEVERITY_TONE_CLASSES[severityCode]}`}>
        {severityMeta.riskLabel}
      </span>
      <span className={`min-w-0 flex-1 truncate text-[13px] ${isActive ? "font-medium text-slate-900" : "text-slate-700"}`}>{problem.title}</span>
      {sources ? <span className="shrink-0 text-slate-400">{sources}</span> : null}
      {isProblemDegraded(problem) ? <span className="shrink-0 rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700">证据不足</span> : null}
      <WorkflowStatusBadge workflowStatus={workflowStatus} />
      {problem.page ? <span className="w-9 shrink-0 text-right text-slate-400">P{problem.page}</span> : null}
    </div>
  );
}

export interface IssueCardProps {
  problem: Problem;
  workflowStatus: string | null;
  onAction: (action: IssueWorkflowAction) => void;
  onSelectEvidenceRef?: (refKey: string) => void;
  isSubmitting: boolean;
  showWorkflowActions?: boolean;
}

export function IssueCard({
  problem,
  workflowStatus,
  onAction,
  onSelectEvidenceRef,
  isSubmitting,
  showWorkflowActions = true,
}: IssueCardProps) {
  const severityCode = normalizeSeverityCode(problem.severity);
  const severityMeta = getSeverityMeta(problem.severity, problem.severityLabel);
  const degraded = isProblemDegraded(problem);
  const sources = sourceLabel(problem);
  const unlocatedReasonLabels = Array.from(new Set([
    ...(problem.evidenceMissing ?? []),
    ...(problem.evidenceVerificationReasons ?? []),
  ])).flatMap((reason) => {
    if (reason.includes("text_not_found_on_page")) return ["AI 引用原文未在对应页找到"];
    if (reason.includes("missing_or_invalid_source_identity")) return ["证据未绑定到当前 PDF 原件"];
    if (reason.includes("missing_bbox")) return ["没有可核验的精确坐标"];
    if (reason.includes("page_out_of_range")) return ["引用页码超出 PDF 页数"];
    return [];
  });
  const isConfirmed = workflowStatus === "confirmed";
  const isIgnored = workflowStatus === "no_issue";
  const roundingCaseRefs = problem.roundingCases?.map((roundingCase) => {
    let refs = (problem.evidenceRefs ?? []).filter((ref) =>
      (ref.relationId && ref.relationId === roundingCase.relationId)
      || (ref.operandId && roundingCase.operandIds.includes(ref.operandId)),
    );
    if (refs.length === 0 && problem.roundingCases?.length === 1) {
      refs = problem.evidenceRefs ?? [];
    }
    return { roundingCase, refs: dedupeEvidenceRefsForDisplay(refs) };
  }) ?? [];
  const displayEvidenceRefs = dedupeEvidenceRefsForDisplay(problem.evidenceRefs ?? []);

  const renderEvidenceRef = (ref: NonNullable<Problem["evidenceRefs"]>[number]) => {
    const roleLabel = ref.role === "parent" ? "合计项"
      : ref.role === "child" ? "明细项"
        : ref.role;
    const detail = [ref.locationText, ref.valueText ? `${ref.valueText} 万元` : ""]
      .filter(Boolean)
      .join(" · ");
    return (
      <button
        key={ref.key}
        type="button"
        onClick={() => onSelectEvidenceRef?.(ref.key)}
        data-testid={`gbc-review-evidence-ref-${problem.id}-${ref.key}`}
        className="rounded border border-info-200 bg-info-50 px-2 py-1 text-left text-xs leading-relaxed text-info-700 hover:bg-info-100"
        title={`${roleLabel} · ${detail}`}
      >
        查看 {roleLabel} · {detail || `第 ${ref.page} 页`}{ref.bbox ? " · 精确定位" : ""}
      </button>
    );
  };

  return (
    <article
      data-testid="gbc-review-issue-detail"
      data-detail-problem-id={problem.id}
      data-workflow-status={workflowStatus ?? "pending"}
      className="flex min-h-full flex-col"
    >
      <div className="flex-1 px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
          <span className={`rounded-full px-2 py-0.5 font-medium ${SEVERITY_TONE_CLASSES[severityCode]}`}>
            {severityMeta.riskLabel}
          </span>
          {sources ? (
            <span className={`rounded-full px-2 py-0.5 font-medium ${problem.source === "ai" ? "bg-primary-100 text-primary-700" : "bg-info-50 text-info-700"}`}>
              来源：{sources}
            </span>
          ) : null}
          {problem.usesManualFill ? (
            <span className="rounded-full bg-warning-100 px-2 py-0.5 font-medium text-warning-700" title="该结论用到了审核员对照原页补录的数据，可在「读取校正」页签查看记录">
              含人工补录数据
            </span>
          ) : null}
          {problem.sourceRelationLabel ? (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600">
              {problem.sourceRelationLabel}
            </span>
          ) : null}
          <span className="font-mono text-slate-400">{problem.ruleId}</span>
          {problem.page ? <span className="text-slate-400">{new Set(problem.evidenceRefs?.map((ref) => ref.page)).size > 1 ? "主证据 " : ""}PDF 第 {problem.page} 页</span> : null}
          {problem.bbox ? <span className="text-success-700">原文已定位</span> : null}
          <WorkflowStatusBadge workflowStatus={workflowStatus} />
        </div>
        {problem.claimedPage && problem.page && problem.claimedPage !== problem.page ? (
          <details className="mb-2 text-xs text-info-700">
            <summary className="cursor-pointer">按 PDF 原文校正页码 · 查看定位记录</summary>
            分析时标注第 {problem.claimedPage} 页，原文在 PDF 第 {problem.page} 页。定位通过仍需核实 AI 判断。
          </details>
        ) : null}

        {degraded ? (
          <div
            className="mb-2 rounded-md bg-warning-50 px-2.5 py-1.5 text-xs font-medium leading-relaxed text-warning-700"
            data-testid={`gbc-review-issue-degraded-badge-${problem.id}`}
          >
            <div>证据不足待复核：本条为 AI 分析结果，因证据不完整已降级，未计入正式问题数</div>
            {unlocatedReasonLabels.length > 0 ? (
              <div className="mt-0.5 font-normal">未绘制定位框：{unlocatedReasonLabels.join("；")}</div>
            ) : null}
          </div>
        ) : null}

        <h4 className="mb-1.5 text-base font-semibold text-slate-900">{problem.title}</h4>
        <p className="mb-3 whitespace-pre-line text-sm leading-relaxed text-slate-700">{problem.description}</p>

        {problem.snippet ? (
          <div className="mb-3 rounded-md bg-surface-100 px-3 py-2 text-sm leading-relaxed text-slate-700">
            <div className="mb-1 text-xs font-medium text-slate-500">{evidenceExcerptLabel(problem.sourceIdentityVerified === true)}</div>
            {problem.sourceIdentityVerified ? `“${problem.snippet}”` : problem.snippet}
          </div>
        ) : null}

        {problem.roundingCases && problem.roundingCases.length > 0 ? (
          <div className="mb-2 space-y-2">
            {roundingCaseRefs.map(({ roundingCase, refs }) => (
              <div key={roundingCase.relationId} className="rounded-md border border-warning-200 bg-warning-50/60 p-2">
                <p className="mb-1 text-sm leading-relaxed text-slate-700">{roundingCase.summary}</p>
                {refs.length > 0 ? <div className="flex flex-wrap gap-1.5">{refs.map(renderEvidenceRef)}</div> : null}
              </div>
            ))}
          </div>
        ) : displayEvidenceRefs.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {displayEvidenceRefs.map(renderEvidenceRef)}
          </div>
        ) : null}
      </div>

      {showWorkflowActions ? <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-white px-4 py-2">
        <button
          type="button"
          onClick={() => onAction("confirm")}
          disabled={isSubmitting || isConfirmed}
          data-testid={`gbc-review-issue-confirm-${problem.id}`}
          className="rounded-md border border-success-200 bg-success-100 px-3 py-1.5 text-xs font-medium text-success-700 transition-colors hover:bg-success-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          确认问题
        </button>
        <button
          type="button"
          onClick={() => onAction("ignore")}
          disabled={isSubmitting || isIgnored}
          data-testid={`gbc-review-issue-ignore-${problem.id}`}
          className="rounded-md border border-slate-200 bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          忽略
        </button>
        <button
          type="button"
          onClick={() => onAction("note")}
          disabled={isSubmitting}
          data-testid={`gbc-review-issue-note-${problem.id}`}
          className="rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          补充意见
        </button>
        {isConfirmed || isIgnored ? (
          <button
            type="button"
            onClick={() => onAction("restore")}
            disabled={isSubmitting}
            data-testid={`gbc-review-issue-restore-${problem.id}`}
            className="rounded-md border border-border px-3 py-1.5 text-xs text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-60"
          >恢复待处理</button>
        ) : null}
      </div> : null}
    </article>
  );
}

export default IssueCard;
