/**
 * IssuesTab：问题总览，以及本地规则/大模型的来源对照视图。
 *
 * 布局是「上列表、下详情」：筛选收在一行（展开后才显示分组按钮），列表一行
 * 一条便于一眼看全，详情只展示当前选中的一条。当前选中项不在列表里时自动
 * 选中第一条，保证详情和左侧原文始终对应同一个问题；↑/↓ 键在列表中切换。
 */
"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { EVIDENCE_STATUS_DEGRADED, problemReviewKind, sortProblemsForReview } from "./reviewWorkbenchAdapters";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssueCard, IssueRow } from "./IssueCard";
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
  onSelectProblem: (problemId: string | null, options?: { keepLayout?: boolean }) => void;
  /** issue_id -> 当前 workflow 状态（confirmed/no_issue/...），未记录时为 undefined。 */
  workflowStatusByIssueId: Record<string, string>;
  onAction: (problem: Problem, action: IssueWorkflowAction) => Promise<boolean>;
  submittingIssueId: string | null;
  lastDecision: IssueDecisionNotice | null;
  onUndo: () => Promise<boolean>;
  pendingObligations: number;
  onOpenObligations: () => void;
  onSelectEvidenceRef: (problem: Problem, refKey: string) => void;
  readOnly?: boolean;
  /** 问题区放大（隐藏原文）时，列表和详情改为左右排布。 */
  wide?: boolean;
}

export interface IssueDecisionNotice {
  problem: Problem;
  previousStatus: string;
  status: string;
}

type SourceView = "all" | "common" | "rule" | "ai" | "other";
type ReviewFilter = "all" | "issue" | "manual_review" | "degraded" | "tip" | "review_notice";
type QueueView = "pending" | "all";
type StatusFilter = "all" | "confirmed" | "no_issue" | "in_package";

const SOURCE_LABELS: Record<SourceView, string> = { all: "全部来源", rule: "本地规则", ai: "大模型", common: "共同发现", other: "系统复核" };
const REVIEW_FILTER_LABELS: Record<ReviewFilter, string> = { all: "全部", issue: "正式问题", manual_review: "待核实", degraded: "证据不足", tip: "提示", review_notice: "复核事项" };
const STATUS_FILTER_LABELS: Record<StatusFilter, string> = { all: "全部状态", confirmed: "已确认", no_issue: "已忽略", in_package: "已进整改包" };

// 与复核工作流共用终态语义；未知状态、待复核和未记录状态仍留在待处理视图。
function isProcessedStatus(status: string | undefined): boolean {
  return status === "confirmed" || status === "no_issue" || status === "in_package";
}

const groups = [
  { kind: "issue", label: "审核问题" },
  { kind: "manual_review", label: "待人工核实" },
  { kind: "degraded", label: "证据降级（不计入问题数）" },
  { kind: "tip", label: "提示" },
  { kind: "review_notice", label: "系统复核事项" },
] as const;

function problemGroup(problem: Problem): string {
  return problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED ? "degraded" : problemReviewKind(problem);
}

function findCard(list: HTMLElement, id: string): HTMLElement | undefined {
  return Array.from(list.querySelectorAll<HTMLElement>("[data-review-problem-id]"))
    .find((card) => card.dataset.reviewProblemId === id);
}

/** 分组标题吸顶，行滚到它下面也算看不见。 */
const STICKY_GROUP_HEADER_HEIGHT = 24;

// 只滚动列表自身：scrollIntoView 会连带滚动 overflow-hidden 的外层容器，把整页布局顶歪。
function keepCardInView(list: HTMLElement, card: HTMLElement) {
  const listBox = list.getBoundingClientRect();
  const cardBox = card.getBoundingClientRect();
  if (cardBox.top < listBox.top + STICKY_GROUP_HEADER_HEIGHT) {
    list.scrollTop -= listBox.top + STICKY_GROUP_HEADER_HEIGHT - cardBox.top;
  } else if (cardBox.bottom > listBox.bottom) {
    list.scrollTop += cardBox.bottom - listBox.bottom;
  }
}

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
  lastDecision,
  onUndo,
  pendingObligations,
  onOpenObligations,
  onSelectEvidenceRef,
  readOnly = false,
  wide = false,
}: IssuesTabProps) {
  const [sourceView, setSourceView] = useState<SourceView>("all");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [showFilters, setShowFilters] = useState(false);
  const [showSourceDetails, setShowSourceDetails] = useState(false);
  // 处理后接续、撤销恢复这些流程自己决定选中哪一条；父组件跟上之前不自动选首条。
  const flowSelectionRef = useRef<string | null | undefined>(undefined);
  const [queueView, setQueueView] = useState<QueueView>("pending");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);
  // 已完成或历史材料默认查看全部记录，不显示一个空的待处理队列。
  const effectiveQueueView = readOnly ? "all" : queueView;
  const listRef = useRef<HTMLDivElement>(null);
  const viewKey = `${effectiveQueueView}:${sourceView}:${reviewFilter}:${statusFilter}`;
  const pendingNavigationRef = useRef<{
    issueId: string; status: string; nextId: string | null; offset: number; viewKey: string;
  } | null>(null);
  const baseProblemIds = useMemo(() => new Set(problems.map((problem) => problem.id)), [problems]);
  const allSortedProblems = useMemo(() => sortProblemsForReview(problems), [problems]);
  const sourceProblems = useMemo(() => {
    return !sourceFindings || sourceView === "all" ? allSortedProblems
      : sortProblemsForReview(sourceView === "common" ? sourceFindings.common
        : sourceView === "rule" ? sourceFindings.rule ?? [...sourceFindings.ruleOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("rule"))]
          : sourceView === "ai" ? sourceFindings.ai ?? [...sourceFindings.aiOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("ai"))]
            : sourceFindings.other);
  }, [allSortedProblems, sourceFindings, sourceView]);
  const classifiedProblems = useMemo(() => {
    return sourceProblems.filter((problem) => {
      if (reviewFilter === "all") return true;
      if (reviewFilter === "degraded") return problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED;
      if (problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED) return false;
      return problemReviewKind(problem) === reviewFilter;
    });
  }, [sourceProblems, reviewFilter]);
  const pendingProblems = useMemo(() => classifiedProblems.filter((problem) =>
    !isProcessedStatus(workflowStatusByIssueId[problem.id])
  ), [classifiedProblems, workflowStatusByIssueId]);
  const visibleProblems = useMemo(() => effectiveQueueView === "pending" ? pendingProblems
    : classifiedProblems.filter((problem) => statusFilter === "all" || workflowStatusByIssueId[problem.id] === statusFilter),
  [effectiveQueueView, pendingProblems, classifiedProblems, statusFilter, workflowStatusByIssueId]);
  // 接续顺序必须与页面分组后的顺序一致，不能只按原始数组找下一条。
  const orderedVisibleProblems = useMemo(() => groups.flatMap(({ kind }) =>
    visibleProblems.filter((problem) => problemGroup(problem) === kind)
  ), [visibleProblems]);

  useLayoutEffect(() => {
    const navigation = pendingNavigationRef.current;
    if (!navigation) return;
    if (navigation.viewKey !== viewKey) {
      pendingNavigationRef.current = null;
      return;
    }
    if (workflowStatusByIssueId[navigation.issueId] !== navigation.status) return;
    pendingNavigationRef.current = null;
    const nextId = orderedVisibleProblems.some((problem) => problem.id === navigation.nextId)
      ? navigation.nextId : orderedVisibleProblems[0]?.id ?? null;
    // 保存成功、卡片移出后的同一帧接续原阅读位置，避免长卡片让用户再次向下找。
    const list = listRef.current;
    const card = list && nextId ? findCard(list, nextId) : undefined;
    if (list && card) {
      list.scrollTop += card.getBoundingClientRect().top - list.getBoundingClientRect().top - navigation.offset;
      card.focus({ preventScroll: true });
    }
    flowSelectionRef.current = nextId;
    onSelectProblem(nextId);
  }, [workflowStatusByIssueId, orderedVisibleProblems, onSelectProblem, viewKey]);

  useLayoutEffect(() => {
    if (!restoreTarget) return;
    const list = listRef.current;
    const card = list ? findCard(list, restoreTarget) : undefined;
    if (!list || !card) return;
    list.scrollTop += card.getBoundingClientRect().top - list.getBoundingClientRect().top;
    card.focus({ preventScroll: true });
    flowSelectionRef.current = restoreTarget;
    onSelectProblem(restoreTarget);
    setRestoreTarget(null);
  }, [restoreTarget, orderedVisibleProblems, onSelectProblem]);

  const detailIndex = Math.max(0, orderedVisibleProblems.findIndex((problem) => problem.id === selectedProblemId));
  const detailProblem = orderedVisibleProblems[detailIndex] ?? null;
  const detailProblemId = detailProblem?.id ?? null;

  useEffect(() => {
    if (flowSelectionRef.current !== undefined) {
      if (flowSelectionRef.current !== selectedProblemId) return;
      flowSelectionRef.current = undefined;
    }
    if (pendingNavigationRef.current || restoreTarget) return;
    if (detailProblemId && detailProblemId !== selectedProblemId) onSelectProblem(detailProblemId, { keepLayout: true });
  }, [detailProblemId, selectedProblemId, restoreTarget, onSelectProblem]);

  useEffect(() => {
    const list = listRef.current;
    const card = list && detailProblemId ? findCard(list, detailProblemId) : undefined;
    if (list && card) keepCardInView(list, card);
  }, [detailProblemId]);

  const moveSelection = (delta: number) => {
    const next = orderedVisibleProblems[detailIndex + delta];
    if (!next) return;
    onSelectProblem(next.id);
    const list = listRef.current;
    const card = list ? findCard(list, next.id) : undefined;
    if (card && list?.contains(document.activeElement)) card.focus({ preventScroll: true });
  };

  const handleAction = async (problem: Problem, action: IssueWorkflowAction) => {
    if (effectiveQueueView === "pending" && (action === "confirm" || action === "ignore")) {
      const index = orderedVisibleProblems.findIndex((item) => item.id === problem.id);
      const remaining = orderedVisibleProblems.filter((item) => item.id !== problem.id);
      const next = remaining[Math.min(Math.max(0, index), remaining.length - 1)];
      const list = listRef.current;
      const card = list ? findCard(list, problem.id) : undefined;
      pendingNavigationRef.current = {
        issueId: problem.id, status: action === "confirm" ? "confirmed" : "no_issue",
        nextId: next?.id ?? null, viewKey,
        offset: list && card ? Math.max(0, card.getBoundingClientRect().top - list.getBoundingClientRect().top) : 0,
      };
    }
    const saved = await onAction(problem, action);
    if (!saved && pendingNavigationRef.current?.issueId === problem.id) pendingNavigationRef.current = null;
    if (saved && action === "restore") {
      setStatusFilter("all");
      setRestoreTarget(problem.id);
    }
  };

  const handleUndo = async () => {
    if (!lastDecision) return;
    const decision = lastDecision;
    if (!await onUndo()) return;
    // 撤销后直接展示恢复的条目，避免它仍被用户后来切换的来源/分类筛选挡住。
    setSourceView("all");
    setReviewFilter("all");
    setStatusFilter("all");
    setQueueView(isProcessedStatus(decision.previousStatus) ? "all" : "pending");
    setRestoreTarget(decision.problem.id);
  };

  const clearFilters = () => {
    setSourceView("all");
    setReviewFilter("all");
    setStatusFilter("all");
  };
  const hasFilters = sourceView !== "all" || reviewFilter !== "all" || (effectiveQueueView === "all" && statusFilter !== "all");
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

  const sourceOptions: Array<[SourceView, number]> = sourceFindings?.enabled ? [
    ["all", problems.length],
    ["rule", sourceFindings.rule?.length ?? sourceFindings.ruleFindingCount],
    ["ai", sourceFindings.ai?.length ?? sourceFindings.aiFindingCount],
    ["common", sourceFindings.commonRelationCount],
    ["other", sourceFindings.other.length],
  ] : [];
  const categoryOptions: Array<[ReviewFilter, number]> = [
    ["all", sourceProblems.length],
    ["issue", verifiedIssueCount],
    ...(candidateCount > 0 ? [["manual_review", candidateCount] as [ReviewFilter, number]] : []),
    ["degraded", degradedCount],
    ["tip", tipCount],
    ["review_notice", noticeCount],
  ];
  const activeFilterLabels = [
    sourceView !== "all" ? `来源：${SOURCE_LABELS[sourceView]}` : null,
    reviewFilter !== "all" ? `类型：${REVIEW_FILTER_LABELS[reviewFilter]}` : null,
    effectiveQueueView === "all" && statusFilter !== "all" ? `状态：${STATUS_FILTER_LABELS[statusFilter]}` : null,
  ].filter((label): label is string => label !== null);
  const chipClass = (active: boolean) => `rounded-md border px-2 py-0.5 text-xs transition-colors ${
    active ? "border-primary-300 bg-primary-50 text-primary-700" : "border-border bg-white text-slate-600 hover:bg-surface-100"
  }`;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="gbc-review-issues-tab">
      <div className="shrink-0 border-b border-border px-3 py-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="flex items-center gap-1.5" aria-label="按处理进度查看">
            {([ ["pending", "待处理", pendingProblems.length], ["all", "全部问题", classifiedProblems.length] ] as Array<[QueueView, string, number]>).map(([id, label, count]) => (
              <button key={id} type="button" aria-pressed={effectiveQueueView === id}
                disabled={readOnly && id === "pending"}
                onClick={() => { setQueueView(id); setStatusFilter("all"); }}
                data-testid={`gbc-review-scope-${id}`}
                className={`rounded-md border px-2.5 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${effectiveQueueView === id ? "border-primary-500 bg-primary-600 text-white" : "border-border text-slate-600 hover:bg-surface-100"}`}
              >{label}（{count}）</button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setShowFilters((value) => !value)}
            aria-expanded={showFilters}
            data-testid="gbc-review-toggle-filters"
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
              showFilters || activeFilterLabels.length > 0 ? "border-primary-300 bg-primary-50 text-primary-700" : "border-border text-slate-600 hover:bg-surface-100"
            }`}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
            筛选{activeFilterLabels.length > 0 ? `（${activeFilterLabels.length}）` : ""} {showFilters ? "⌃" : "⌄"}
          </button>
          {!showFilters && activeFilterLabels.length > 0 ? (
            <span className="min-w-0 truncate text-xs text-primary-700" data-testid="gbc-review-active-filters">{activeFilterLabels.join(" · ")}</span>
          ) : null}
          {hasFilters ? <button type="button" onClick={clearFilters} data-testid="gbc-review-clear-filters" className="text-xs text-primary-700 hover:underline">清除筛选</button> : null}
          <span className="ml-auto text-[11px] text-slate-500" data-testid="gbc-review-issues-count">正式问题（{verifiedIssueCount}）· 显示 {visibleProblems.length} 条</span>
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
        {showFilters ? (
          <div className="mt-1.5 space-y-1.5 rounded-md border border-border bg-surface-50 px-2.5 py-2" data-testid="gbc-review-filter-panel">
            {effectiveQueueView === "all" ? (
              <div className="flex flex-wrap items-center gap-1.5" aria-label="按处理状态筛选">
                <span className="mr-1 w-14 text-xs text-slate-500">处理状态</span>
                {(Object.keys(STATUS_FILTER_LABELS) as StatusFilter[]).map((id) => (
                  <button key={id} type="button" aria-pressed={statusFilter === id} onClick={() => setStatusFilter(id)}
                    data-testid={`gbc-review-status-${id}`}
                    className={chipClass(statusFilter === id)}
                  >{STATUS_FILTER_LABELS[id]} <span className="opacity-75">{classifiedProblems.filter((problem) => id === "all" || workflowStatusByIssueId[problem.id] === id).length}</span></button>
                ))}
              </div>
            ) : null}
            {sourceFindings?.enabled ? (
              <div className="flex flex-wrap items-center gap-1.5" aria-label="按检查来源筛选问题" data-testid="gbc-review-source-filter">
                <span className="mr-1 w-14 text-xs text-slate-500">发现来源</span>
                {sourceOptions.map(([id, count]) => (
                  <button key={id} type="button" aria-pressed={sourceView === id}
                    onClick={() => setSourceView(id)} data-testid={`gbc-review-source-${id}`}
                    className={chipClass(sourceView === id)}
                  >{SOURCE_LABELS[id]} <span className="opacity-75">{count}</span></button>
                ))}
              </div>
            ) : null}
            <div className="flex flex-wrap items-center gap-1.5" aria-label="按结果分类筛选">
              <span className="mr-1 w-14 text-xs text-slate-500">结果类型</span>
              {categoryOptions.map(([id, count]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={reviewFilter === id}
                  onClick={() => setReviewFilter(id)}
                  data-testid={`gbc-review-category-${id}`}
                  className={chipClass(reviewFilter === id)}
                >{REVIEW_FILTER_LABELS[id]} <span className="opacity-75">{count}</span></button>
              ))}
            </div>
            <p className="text-[11px] text-slate-500">当前来源结果 {sourceProblems.length} 条{hasFilters ? "；数量按当前来源和结果类型筛选统计" : ""}。</p>
          </div>
        ) : null}
        {sourceFindings?.enabled && showSourceDetails ? (
          <div className="mt-2 rounded-md bg-surface-100 px-3 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600" data-testid="gbc-review-source-summary">
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
      {lastDecision ? (
        <div role="status" className="flex shrink-0 items-center gap-2 border-b border-success-200 bg-success-50 px-3 py-2 text-xs text-success-700" data-testid="gbc-review-decision-notice">
          <span className="min-w-0 flex-1 truncate" title={lastDecision.problem.title}>{lastDecision.status === "confirmed" ? "已确认该问题" : lastDecision.status === "no_issue" ? "已忽略该问题" : "已恢复为待处理"}：{lastDecision.problem.title}</span>
          {!readOnly ? <button type="button" onClick={() => void handleUndo()} disabled={submittingIssueId !== null} data-testid="gbc-review-undo" className="shrink-0 rounded px-1 font-medium underline disabled:cursor-not-allowed disabled:opacity-50">撤销</button> : null}
        </div>
      ) : null}
      {visibleProblems.length === 0 ? (
        <div className="flex-1 p-6 text-center text-sm text-slate-500" data-testid="gbc-review-issues-empty">
          <p>{effectiveQueueView === "pending" && classifiedProblems.length > 0 ? "当前筛选下已无待处理项。" : effectiveQueueView === "all" && statusFilter !== "all" ? "当前筛选下没有该处理状态的条目。" : emptyText}</p>
          {effectiveQueueView === "pending" ? <button type="button" onClick={() => { setQueueView("all"); setStatusFilter("all"); }} className="mt-3 text-primary-700 hover:underline" data-testid="gbc-review-view-all">查看全部问题</button> : null}
          {pendingObligations > 0 ? <p className="mt-3 text-xs">仍有 {pendingObligations} 项检查待补核。<button type="button" onClick={onOpenObligations} className="ml-1 text-primary-700 hover:underline" data-testid="gbc-review-open-obligations">前往检查补核</button></p> : null}
        </div>
      ) : (
        <div className={`min-h-0 min-w-0 flex-1 ${wide ? "grid grid-cols-[minmax(260px,2fr)_minmax(0,3fr)] grid-rows-[minmax(0,1fr)]" : "flex flex-col"}`}>
          <div
            ref={listRef}
            onKeyDown={(event) => {
              if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
              event.preventDefault();
              moveSelection(event.key === "ArrowDown" ? 1 : -1);
            }}
            className={`min-h-0 min-w-0 overflow-y-auto px-2 pb-2 ${wide ? "border-r border-border" : "max-h-[40%] shrink-0 border-b border-border"}`}
            data-testid="gbc-review-issues-list"
          >
            {groups.map(({ kind, label }) => {
              const items = visibleProblems.filter((problem) => problemGroup(problem) === kind);
              if (items.length === 0) return null;
              return (
                <section key={kind} data-testid={`gbc-review-group-${kind}`}>
                  <h3 className="sticky top-0 z-10 bg-white px-1 pb-1 pt-1.5 text-[11px] font-medium text-slate-500">{label}（{items.length}）</h3>
                  <div className="space-y-0.5">
                    {items.map((problem) => (
                      <IssueRow
                        key={problem.id}
                        problem={problem}
                        isActive={problem.id === selectedProblemId}
                        workflowStatus={workflowStatusByIssueId[problem.id] ?? null}
                        onSelect={() => onSelectProblem(problem.id)}
                      />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="gbc-review-issue-detail-pane">
            {detailProblem ? (
              <>
                <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-border bg-surface-50 px-3 py-1 text-xs text-slate-500">
                  <span className="mr-1" data-testid="gbc-review-issue-position">第 {detailIndex + 1} / {orderedVisibleProblems.length} 条</span>
                  <button type="button" onClick={() => moveSelection(-1)} disabled={detailIndex <= 0} data-testid="gbc-review-issue-prev"
                    className="rounded px-1.5 py-0.5 text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-40">上一条</button>
                  <button type="button" onClick={() => moveSelection(1)} disabled={detailIndex >= orderedVisibleProblems.length - 1} data-testid="gbc-review-issue-next"
                    className="rounded px-1.5 py-0.5 text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-40">下一条</button>
                  <span className="hidden text-[11px] text-slate-400 xl:inline">列表中可按 ↑ ↓ 切换</span>
                  {detailProblem.page ? (
                    <button type="button" onClick={() => onSelectProblem(detailProblem.id)} data-testid="gbc-review-issue-locate"
                      className="ml-auto rounded px-1.5 py-0.5 text-primary-700 hover:bg-primary-50">定位原文 · 第 {detailProblem.page} 页</button>
                  ) : null}
                </div>
                <div key={detailProblem.id} className="min-h-0 flex-1 overflow-y-auto">
                  <IssueCard
                    problem={detailProblem}
                    workflowStatus={workflowStatusByIssueId[detailProblem.id] ?? null}
                    onAction={(action) => void handleAction(detailProblem, action)}
                    onSelectEvidenceRef={(refKey) => onSelectEvidenceRef(detailProblem, refKey)}
                    isSubmitting={submittingIssueId !== null}
                    showWorkflowActions={!readOnly && (sourceView === "all" || baseProblemIds.has(detailProblem.id))}
                  />
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

export default IssuesTab;
