/** IssuesTab：问题总览，以及本地规则/大模型的来源对照视图。 */
"use client";

import { useMemo, useState } from "react";

import { EVIDENCE_STATUS_DEGRADED, problemReviewKind, sortProblemsForReview } from "./reviewWorkbenchAdapters";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssueCard } from "./IssueCard";
import type { Problem } from "../../../lib/mock";

export interface SourceFindingViews {
  enabled: boolean;
  common: Problem[];
  ai?: Problem[];
  rule?: Problem[];
  aiOnly: Problem[];
  ruleOnly: Problem[];
  other: Problem[];
  aiFindingCount: number;
  ruleFindingCount: number;
  commonRelationCount: number;
  aiState?: string;
  aiModel?: string;
}

export interface IssuesTabProps {
  problems: Problem[];
  sourceFindings?: SourceFindingViews;
  selectedProblemId: string | null;
  onSelectProblem: (problemId: string) => void;
  /** issue_id -> 当前 workflow 状态（confirmed/no_issue/...），未记录时为 undefined。 */
  workflowStatusByIssueId: Record<string, string>;
  onAction: (problem: Problem, action: IssueWorkflowAction) => void;
  submittingIssueId: string | null;
  onSelectEvidenceRef: (problem: Problem, refKey: string) => void;
  readOnly?: boolean;
}

type SourceView = "all" | "common" | "rule" | "ai" | "other";
type ReviewFilter = "all" | "issue" | "manual_review" | "degraded" | "tip" | "review_notice";

function aiStateLabel(state: string | undefined): string {
  if (state === "succeeded") return "已完成";
  if (state === "degraded") return "未完整完成";
  if (state === "running" || state === "processing") return "检查中";
  if (state === "failed" || state === "fallback") return "未成功";
  if (state === "skipped" || state === "not_requested") return "未启用";
  return "状态未知";
}

export function IssuesTab({
  problems,
  sourceFindings,
  selectedProblemId,
  onSelectProblem,
  workflowStatusByIssueId,
  onAction,
  submittingIssueId,
  onSelectEvidenceRef,
  readOnly = false,
}: IssuesTabProps) {
  const [sourceView, setSourceView] = useState<SourceView>("all");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [showSourceDetails, setShowSourceDetails] = useState(false);
  const baseProblemIds = useMemo(() => new Set(problems.map((problem) => problem.id)), [problems]);
  const allSortedProblems = useMemo(() => sortProblemsForReview(problems), [problems]);
  const sourceProblems = useMemo(() => {
    return !sourceFindings || sourceView === "all" ? allSortedProblems
      : sortProblemsForReview(sourceView === "common" ? sourceFindings.common
        : sourceView === "rule" ? sourceFindings.rule ?? [...sourceFindings.ruleOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("rule"))]
          : sourceView === "ai" ? sourceFindings.ai ?? [...sourceFindings.aiOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("ai"))]
            : sourceFindings.other);
  }, [allSortedProblems, sourceFindings, sourceView]);
  const visibleProblems = useMemo(() => {
    return sourceProblems.filter((problem) => {
      if (reviewFilter === "all") return true;
      if (reviewFilter === "degraded") return problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED;
      if (problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED) return false;
      return problemReviewKind(problem) === reviewFilter;
    });
  }, [sourceProblems, reviewFilter]);

  const groups = [
    { kind: "issue", label: "审核问题" },
    { kind: "manual_review", label: "待人工核实" },
    { kind: "degraded", label: "证据降级（不计入问题数）" },
    { kind: "tip", label: "提示" },
    { kind: "review_notice", label: "系统复核事项" },
  ] as const;
  const verifiedIssueCount = sourceProblems.filter((problem) => problemReviewKind(problem) === "issue" && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED).length;
  const candidateCount = sourceProblems.filter((problem) =>
    problemReviewKind(problem) === "manual_review" && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED
  ).length;
  const degradedCount = sourceProblems.filter((problem) => problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED).length;
  const tipCount = sourceProblems.filter((problem) => problemReviewKind(problem) === "tip" && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED).length;
  const noticeCount = sourceProblems.filter((problem) => problemReviewKind(problem) === "review_notice" && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED).length;
  const emptyText = reviewFilter !== "all" ? "当前分类与来源筛选下没有检查结果。" : sourceView === "ai" && sourceFindings?.enabled
    ? sourceFindings.aiState === "succeeded" && sourceFindings.aiFindingCount === 0
      ? "大模型检查已完成，但系统没有收到可展示的大模型发现。"
      : sourceFindings.aiFindingCount > 0
        ? "当前没有可展示的大模型结果。"
        : sourceFindings.aiState === "running" || sourceFindings.aiState === "processing"
          ? "大模型仍在检查，完成后这里会显示大模型发现的问题。"
          : sourceFindings.aiState === "failed" || sourceFindings.aiState === "fallback"
            ? "大模型检查未成功完成，请查看检查阶段记录。"
            : "当前没有大模型发现的问题。"
    : sourceView === "common"
      ? "当前没有建立明确关系的共同发现。"
      : sourceView === "rule"
        ? "当前没有本地规则发现的问题。"
      : sourceView === "other"
        ? "当前没有其他系统复核事项。"
        : "当前任务暂无问题。";

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="gbc-review-issues-tab">
      <div className="shrink-0 border-b border-border px-3 py-1.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs font-medium text-slate-700" data-testid="gbc-review-issues-count">
            当前来源结果（{sourceProblems.length}）<span className="ml-2 font-normal text-slate-500">正式问题（{verifiedIssueCount}）</span>
          </div>
          {sourceFindings?.enabled ? (
            <button
              type="button"
              onClick={() => setShowSourceDetails((value) => !value)}
              aria-expanded={showSourceDetails}
              data-testid="gbc-review-toggle-source-details"
              className="rounded px-1 py-0.5 text-xs text-slate-500 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >来源与分类说明 {showSourceDetails ? "⌃" : "⌄"}</button>
          ) : null}
        </div>
        {sourceFindings?.enabled ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-label="按检查来源筛选问题" data-testid="gbc-review-source-filter">
            <span className="mr-1 text-xs font-medium text-slate-500">发现来源</span>
            {([
              ["all", "全部来源", problems.length],
              ["rule", "本地规则", sourceFindings.rule?.length ?? sourceFindings.ruleFindingCount],
              ["ai", "大模型", sourceFindings.ai?.length ?? sourceFindings.aiFindingCount],
              ["common", "共同发现", sourceFindings.commonRelationCount],
              ["other", "系统复核", sourceFindings.other.length],
            ] as Array<[SourceView, string, number]>).map(([id, label, count]) => (
              <button key={id} type="button" aria-pressed={sourceView === id}
                onClick={() => setSourceView(id)} data-testid={`gbc-review-source-${id}`}
                className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition-colors ${sourceView === id ? "border-primary-500 bg-primary-600 text-white" : "border-border bg-white text-slate-600 hover:bg-surface-100"}`}
              >{label} <span className="opacity-80">{count}</span></button>
            ))}
          </div>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-label="按结果分类筛选">
          <span className="mr-1 text-xs text-slate-500">处理分类</span>
          {([
            ["all", "全部", sourceProblems.length],
            ["issue", "正式问题", verifiedIssueCount],
            ...(candidateCount > 0 ? [["manual_review", "待核实", candidateCount]] : []),
            ["degraded", "证据不足", degradedCount],
            ["tip", "提示", tipCount],
            ["review_notice", "复核事项", noticeCount],
          ] as Array<[ReviewFilter, string, number]>).map(([id, label, count]) => (
            <button
              key={id}
              type="button"
              aria-pressed={reviewFilter === id}
              onClick={() => setReviewFilter(id)}
              data-testid={`gbc-review-category-${id}`}
              className={`rounded-md border px-2 py-1 text-xs transition-colors ${reviewFilter === id ? "border-primary-300 bg-primary-50 text-primary-700" : "border-border text-slate-600 hover:bg-surface-100"}`}
            >{label} <span className="opacity-75">{count}</span></button>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
          来源表示谁发现；分类表示如何处理。
        </p>
        {sourceFindings?.enabled && showSourceDetails ? (
          <div className="mt-2 rounded-md bg-surface-100 px-3 py-2">
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600" data-testid="gbc-review-source-summary">
              <span>大模型：{aiStateLabel(sourceFindings.aiState)} · {sourceFindings.aiFindingCount} 条发现</span>
              {sourceFindings.aiModel ? <span>模型：{sourceFindings.aiModel}</span> : null}
              <span>本地规则：{sourceFindings.ruleFindingCount} 条发现</span>
              <span>其他复核项：{sourceFindings.other.length} 条</span>
            </div>
            <div className="mt-1 text-xs text-slate-500">本地规则和大模型各自展示完整结果，包含共同发现；两侧数量不能直接相加。共同发现按明确的检查关系，或原件中同一处已核验的重复词匹配；原文相同不代表 AI 结论已经确认。</div>
            <div className="mt-1 text-xs text-slate-500">正式问题有可核验证据；待核实需人工判断；证据不足未完成原文核验；提示供参考；复核事项是系统检查待办。</div>
          </div>
        ) : null}
            {sourceView === "ai" && sourceFindings?.aiState === "succeeded" && sourceFindings.aiFindingCount === 0 ? (
              <div className="mt-2 rounded-md border border-info-200 bg-info-50 px-2.5 py-2 text-[11px] leading-relaxed text-info-700" data-testid="gbc-review-ai-empty-completed">
                大模型检查已完成，但没有收到可展示的问题。这个状态表示本次 AI 结果为 0 条，不代表本地规则没有发现问题。
              </div>
            ) : null}
            {sourceFindings?.aiState === "degraded" ? (
              <div className="mt-2 rounded-md border border-warning-200 bg-warning-50 px-2.5 py-2 text-[11px] leading-relaxed text-warning-700" data-testid="gbc-review-ai-degraded">
                大模型检查未完整完成。本次保存了 {sourceFindings.aiFindingCount} 条大模型发现；请在“大模型”中查看，并结合证据状态复核。
              </div>
            ) : null}
      </div>
      {visibleProblems.length === 0 ? (
        <div className="flex-1 p-6 text-center text-sm text-slate-500" data-testid="gbc-review-issues-empty">
          {emptyText}
        </div>
      ) : (
        <div className="min-h-0 min-w-0 flex-1 space-y-2 overflow-y-auto px-3 pb-3" data-testid="gbc-review-issues-list">
          {groups.map(({ kind, label }) => {
            const items = visibleProblems.filter((problem) => {
              if (kind === "degraded") return problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED;
              if (problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED) return false;
              if (kind === "manual_review") {
                return problemReviewKind(problem) === kind && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED;
              }
              return problemReviewKind(problem) === kind;
            });
            if (items.length === 0) return null;
            return (
              <section key={kind} data-testid={`gbc-review-group-${kind}`} className="space-y-2">
                <h3 className="pt-2 text-xs font-medium text-slate-500">{label}（{items.length}）</h3>
                {items.map((problem) => (
                  <IssueCard
                    key={problem.id}
                    problem={problem}
                    isActive={problem.id === selectedProblemId}
                    workflowStatus={workflowStatusByIssueId[problem.id] ?? null}
                    onSelect={() => onSelectProblem(problem.id)}
                    onAction={(action) => onAction(problem, action)}
                    onSelectEvidenceRef={(refKey) => onSelectEvidenceRef(problem, refKey)}
                    isSubmitting={submittingIssueId === problem.id}
                    showWorkflowActions={!readOnly && (sourceView === "all" || baseProblemIds.has(problem.id))}
                  />
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default IssuesTab;
