/**
 * IssuesTab：右栏 tab 1「审核问题（N）」（Task 6.4）。
 *
 * N 的口径：countFormalProblems()，与后端 count_formal_findings 完全一致
 * （降级问题不计入 N，但仍在列表里展示并带降级标识——用户有权知道存在这类问题，
 * 只是不把它们算作"正式问题"）。排序用 sortProblemsForReview()（按页码升序）。
 */
"use client";

import { countFormalProblems, sortProblemsForReview } from "./reviewWorkbenchAdapters";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssueCard } from "./IssueCard";
import type { Problem } from "../../../lib/mock";

export interface IssuesTabProps {
  problems: Problem[];
  selectedProblemId: string | null;
  onSelectProblem: (problemId: string) => void;
  /** issue_id -> 当前 workflow 状态（confirmed/no_issue/...），未记录时为 undefined。 */
  workflowStatusByIssueId: Record<string, string>;
  onAction: (problem: Problem, action: IssueWorkflowAction) => void;
  submittingIssueId: string | null;
}

export function IssuesTab({
  problems,
  selectedProblemId,
  onSelectProblem,
  workflowStatusByIssueId,
  onAction,
  submittingIssueId,
}: IssuesTabProps) {
  const formalCount = countFormalProblems(problems);
  const sortedProblems = sortProblemsForReview(problems);

  return (
    <div className="flex h-full flex-col" data-testid="gbc-review-issues-tab">
      <div className="shrink-0 px-3 py-2 text-xs font-medium text-slate-500" data-testid="gbc-review-issues-count">
        审核问题（{formalCount}）
      </div>
      {sortedProblems.length === 0 ? (
        <div className="flex-1 p-6 text-center text-sm text-slate-400" data-testid="gbc-review-issues-empty">
          当前任务暂无问题。
        </div>
      ) : (
        <div className="flex-1 space-y-2 overflow-y-auto px-3 pb-3">
          {sortedProblems.map((problem) => (
            <IssueCard
              key={problem.id}
              problem={problem}
              isActive={problem.id === selectedProblemId}
              workflowStatus={workflowStatusByIssueId[problem.id] ?? null}
              onSelect={() => onSelectProblem(problem.id)}
              onAction={(action) => onAction(problem, action)}
              isSubmitting={submittingIssueId === problem.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default IssuesTab;
