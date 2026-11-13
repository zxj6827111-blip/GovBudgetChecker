/**
 * IssuesTab：当前问题的完整解释，以及“必须处理 / 参考”两条队列。
 *
 * 解释区占满问题栏，问题列表只在点击「问题列表」时盖在解释区上，选中后收起；
 * 平时用上一条/下一条逐条阅读。必须处理队列与后端完成门禁同口径
 * （isFormalProblem，对应 review_problem_set.is_blocking_candidate），参考队列
 * 是其余条目，不计入完成条件。当前选中项不在队列里时自动选中第一条，保证解释
 * 和左侧原文始终对应同一个问题；列表展开时 ↑/↓ 键切换。
 */
"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ListFilter, Maximize2, Minimize2, Type, X } from "lucide-react";

import {
  EVIDENCE_STATUS_DEGRADED,
  isFormalProblem,
  problemReviewKind,
  sortProblemsForReview,
} from "./reviewWorkbenchAdapters";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssueCard, IssueDecisionFooter, IssueRow } from "./IssueCard";
import { IgnoreReasonPanel } from "./IgnoreReasonPanel";
import { ObligationCard } from "./ObligationCard";
import { ObligationDecisionPanel, type ObligationPanelDecision } from "./ObligationDecisionPanel";
import { isObligationProblem, obligationAutoEvidence, readableObligationReason, type ObligationDecision } from "./obligationProblems";
import { parseDecisionNote, type IgnoreReason } from "./reviewDecisionNotes";
import type { ObligationCascadeItemRecord, ObligationReviewItemRecord } from "./reviewLifecycleAdapters";
import type { Problem } from "../../../lib/mock";
import { groupProblemsByCategory, issueCategory } from "../../../lib/issueCategories";

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
  aiCoverage?: {
    status?: string;
    planned_calls?: number;
    completed_calls?: number;
    completed_calls_includes_failed?: boolean;
    finished_calls?: number;
    succeeded_calls?: number;
    effective_completed_calls?: number;
    failed_calls?: number;
    valid_empty_calls?: number;
    partial_calls?: number;
    invalid_calls?: number;
    truncated_calls?: number;
    retried_calls?: number;
    retry_attempts?: number;
    failed_units?: Array<{
      specialty?: string;
      window_id?: number;
      pages?: number[];
      status?: string;
      error_type?: string | null;
      http_status?: number | null;
      retryable?: boolean | null;
      attempts?: number | null;
      retry_count?: number | null;
    }>;
    pages_total?: number;
    pages_covered?: number[];
    uncovered_ranges?: Array<{ start?: number; end?: number; pages?: number[]; reason?: string }>;
    capabilities?: Record<string, string>;
    coverage_note?: string;
  };
}

export type ReviewQueue = "required" | "reference";
export type ReviewTextSize = "standard" | "large";
export const REVIEW_TEXT_SIZE_PX: Record<ReviewTextSize, number> = { standard: 15, large: 17 };

export interface IssuesTabProps {
  problems: Problem[];
  sourceFindings?: SourceFindingViews;
  /** 检查范围：未完整完成时在问题区顶部展开说明，完整完成时只在页面状态条显示。 */
  queue: ReviewQueue;
  onOpenRelatedQueue?: (queue: ReviewQueue, problemId: string) => void;
  selectedProblemId: string | null;
  onSelectProblem: (problemId: string | null, options?: { keepLayout?: boolean }) => void;
  selectedEvidenceRefKey: string | null;
  /** issue_id -> 当前 workflow 状态（confirmed/no_issue/...），未记录时为 undefined。 */
  workflowStatusByIssueId: Record<string, string>;
  workflowNotes: Record<string, string>;
  workflowInheritedByIssueId?: Record<string, boolean>;
  priorDecisions?: Array<{ issue_id: string; title?: string; status: string; note?: string; page?: number }>;
  onAction: (problem: Problem, action: IssueWorkflowAction, ignoreReason?: IgnoreReason) => Promise<boolean>;
  submittingIssueId: string | null;
  lastDecision: IssueDecisionNotice | null;
  onUndo: () => Promise<boolean>;
  /** 「未能自动核对」条目（补核义务）的原记录，键为条目 id。 */
  obligationItems?: Record<string, ObligationReviewItemRecord>;
  /** 根问题 id → 因它（缺表/空表）无法自动进行的核对。 */
  cascadedChecks?: Record<string, ObligationCascadeItemRecord[]>;
  /** 提交补核结论；返回是否已保存（用于处理后接续下一条）。 */
  onObligationDecision?: (problem: Problem, decision: ObligationDecision, note: string, evidenceReference: string, scopeConfirmed: boolean) => Promise<boolean>;
  /** 文种口径类补核需要先确认材料口径时的入口。 */
  onOpenScope?: () => void;
  onSelectEvidenceRef: (problem: Problem, refKey: string) => void;
  readOnly?: boolean;
  /** 不能提交判定时，操作栏位置显示的说明（预览、失效、历史、已完成）。 */
  readOnlyHint?: { text: string; invalidatedReason: string | null; completed?: boolean } | null;
  savedAtText?: string | null;
  /** 外部请求的来源筛选（如从「AI 文字检查」点“查看大模型参考条目”），nonce 变化即生效。 */
  sourceRequest?: { value: SourceView; nonce: number } | null;
  textSize: ReviewTextSize;
  onToggleTextSize: () => void;
  /** 放大解释：原文栏变窄但保留，解释区变宽。 */
  focusMode: boolean;
  onToggleFocus: () => void;
}

export interface IssueDecisionNotice {
  problem: Problem;
  previousStatus: string;
  /** 撤销时连同 note 一起恢复（忽略理由写在 note 里）。 */
  previousNote?: string;
  status: string;
}

type SourceView = "all" | "common" | "rule" | "ai" | "other";
export type { SourceView };
type ReviewFilter = "all" | "issue" | "obligation" | "manual_review" | "degraded" | "tip" | "review_notice";
type QueueView = "pending" | "all";
type StatusFilter = "all" | "confirmed" | "no_issue" | "in_package";

const SOURCE_LABELS: Record<SourceView, string> = { all: "全部来源", rule: "本地规则", ai: "大模型", common: "共同发现", other: "系统复核" };
const REVIEW_FILTER_LABELS: Record<ReviewFilter, string> = { all: "全部", issue: "正式问题", obligation: "未能自动核对", manual_review: "待核实", degraded: "证据不足", tip: "提示", review_notice: "复核事项" };
const STATUS_FILTER_LABELS: Record<StatusFilter, string> = { all: "全部状态", confirmed: "已确认", no_issue: "已忽略", in_package: "已进整改包" };

// 与复核工作流共用终态语义；未知状态、待复核和未记录状态仍留在待处理视图。
// 「未能自动核对」条目的状态是补核决定码，三种结论同样算已处理。
const PROCESSED_STATUSES = new Set(["confirmed", "no_issue", "in_package", "verified_ok", "verified_issue", "not_applicable"]);
function isProcessedStatus(status: string | undefined): boolean {
  return PROCESSED_STATUSES.has(status ?? "");
}

function findCard(list: HTMLElement, id: string): HTMLElement | undefined {
  return Array.from(list.querySelectorAll<HTMLElement>("[data-review-problem-id]"))
    .find((card) => card.dataset.reviewProblemId === id);
}

/** 分组标题吸顶，行滚到它下面也算看不见。 */
const STICKY_GROUP_HEADER_HEIGHT = 56;

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

function aiCoverageLabel(status: string | undefined): string {
  if (status === "complete") return "覆盖完整";
  if (status === "partial") return "部分覆盖";
  if (status === "failed") return "执行失败";
  if (status === "not_run") return "未执行";
  if (status === "unknown") return "覆盖未知";
  return "覆盖未记录";
}

export function IssuesTab({
  problems,
  sourceFindings,
  queue,
  onOpenRelatedQueue,
  selectedProblemId,
  onSelectProblem,
  selectedEvidenceRefKey,
  workflowStatusByIssueId,
  workflowNotes,
  workflowInheritedByIssueId = {},
  priorDecisions = [],
  onAction,
  submittingIssueId,
  lastDecision,
  onUndo,
  obligationItems = {},
  cascadedChecks = {},
  onObligationDecision,
  onOpenScope,
  onSelectEvidenceRef,
  readOnly = false,
  readOnlyHint = null,
  savedAtText = null,
  sourceRequest = null,
  textSize,
  onToggleTextSize,
  focusMode,
  onToggleFocus,
}: IssuesTabProps) {
  const [sourceView, setSourceView] = useState<SourceView>("all");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [showSourceDetails, setShowSourceDetails] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  // 列表与忽略面板都记在打开时的队列上：切换队列即自然收起，不靠事后的 effect
  // 去关（effect 在绘制后才执行，会把用户紧接着点开的列表又关掉）。
  const [listOpenFor, setListOpenFor] = useState<ReviewQueue | null>(null);
  const listOpen = listOpenFor === queue;
  const setListOpen = (open: boolean) => setListOpenFor(open ? queue : null);
  const [ignoreState, setIgnoreState] = useState<{ problem: Problem; queue: ReviewQueue } | null>(null);
  const ignoreTarget = ignoreState?.queue === queue ? ignoreState.problem : null;
  const setIgnoreTarget = (problem: Problem | null) => setIgnoreState(problem ? { problem, queue } : null);
  // 补核结论需要文字依据时的填写面板（确认有问题 / 不适用 / 无定位的核对无误）。
  const [obligationPanel, setObligationPanel] = useState<{ problem: Problem; decision: ObligationPanelDecision; queue: ReviewQueue } | null>(null);
  const obligationPanelTarget = obligationPanel?.queue === queue ? obligationPanel : null;
  const [obligationSubmitting, setObligationSubmitting] = useState(false);
  // 处理后接续、撤销恢复这些流程自己决定选中哪一条；父组件跟上之前不自动选首条。
  const flowSelectionRef = useRef<string | null | undefined>(undefined);
  const [queueView, setQueueView] = useState<QueueView>("pending");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null);
  // 外部跳转（例如从「AI 文字检查」点“查看大模型参考条目”）要直接落到大模型来源，
  // 而不是让用户在全量参考队列里逐条翻找；nonce 保证同一筛选可以重复触发。
  useEffect(() => {
    if (!sourceRequest) return;
    setSourceView(sourceRequest.value);
    setReviewFilter("all");
    setStatusFilter("all");
  }, [sourceRequest]);
  // 已完成或历史材料默认查看全部记录，不显示一个空的待处理队列。
  const effectiveQueueView = readOnly ? "all" : queueView;
  const listRef = useRef<HTMLDivElement>(null);
  const readingRef = useRef<HTMLDivElement>(null);
  const viewKey = `${queue}:${effectiveQueueView}:${sourceView}:${reviewFilter}:${statusFilter}`;
  const pendingNavigationRef = useRef<{
    issueId: string; status: string; nextId: string | null; viewKey: string;
  } | null>(null);
  const baseProblemIds = useMemo(() => new Set(problems.map((problem) => problem.id)), [problems]);
  // 与后端完成门禁同口径：只有正式问题必须表态。
  const requiredIds = useMemo(() => new Set(problems.filter(isFormalProblem).map((problem) => problem.id)), [problems]);
  const allSortedProblems = useMemo(() => sortProblemsForReview(problems), [problems]);

  // 类型筛选按队列不同，切换队列时在渲染中清掉，避免停在另一队列才有的类型上。
  const [filterQueue, setFilterQueue] = useState(queue);
  if (filterQueue !== queue) {
    setFilterQueue(queue);
    setReviewFilter("all");
  }

  // 每个来源按当前队列取条目：来源按钮上的数字与点开后看到的条数必须一致，
  // 不能把另一队列的条目也算进来。
  const sourceBuckets = useMemo(() => {
    const inQueue = (items: Problem[]) => items.filter((problem) => requiredIds.has(problem.id) === (queue === "required"));
    const all = inQueue(allSortedProblems);
    if (!sourceFindings) return { all, rule: all, ai: all, common: all, other: all } as Record<SourceView, Problem[]>;
    return {
      all,
      common: inQueue(sortProblemsForReview(sourceFindings.common)),
      rule: inQueue(sortProblemsForReview(sourceFindings.rule ?? [...sourceFindings.ruleOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("rule"))])),
      ai: inQueue(sortProblemsForReview(sourceFindings.ai ?? [...sourceFindings.aiOnly, ...sourceFindings.common.filter((item) => item.findingSources?.includes("ai"))])),
      other: inQueue(sortProblemsForReview(sourceFindings.other)),
    } as Record<SourceView, Problem[]>;
  }, [allSortedProblems, sourceFindings, queue, requiredIds]);
  const sourceProblems = sourceBuckets[sourceView];
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
  const categoryGroups = useMemo(() => groupProblemsByCategory(visibleProblems), [visibleProblems]);
  const orderedVisibleProblems = useMemo(() => categoryGroups.flatMap((group) => group.items), [categoryGroups]);

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
    flowSelectionRef.current = nextId;
    onSelectProblem(nextId);
  }, [workflowStatusByIssueId, orderedVisibleProblems, onSelectProblem, viewKey]);

  useLayoutEffect(() => {
    if (!restoreTarget) return;
    if (!orderedVisibleProblems.some((problem) => problem.id === restoreTarget)) return;
    flowSelectionRef.current = restoreTarget;
    onSelectProblem(restoreTarget);
    setRestoreTarget(null);
  }, [restoreTarget, orderedVisibleProblems, onSelectProblem]);

  const detailIndex = Math.max(0, orderedVisibleProblems.findIndex((problem) => problem.id === selectedProblemId));
  const detailProblem = orderedVisibleProblems[detailIndex] ?? null;
  const detailProblemId = detailProblem?.id ?? null;
  const detailCategory = detailProblem ? issueCategory(detailProblem) : null;
  const detailCategoryExpanded = Boolean(detailCategory && expandedCategories.has(`${queue}:${detailCategory.id}`));
  const detailCategoryGroup = categoryGroups.find((group) => group.id === detailCategory?.id);
  const relatedQueueProblems = useMemo(() => allSortedProblems.filter((problem) =>
    requiredIds.has(problem.id) !== (queue === "required")
      && issueCategory(problem).id === detailCategory?.id
  ), [allSortedProblems, requiredIds, queue, detailCategory?.id]);

  // 当前项、键盘接续与撤销恢复都展开所属类别；折叠只影响浏览，不改变待办。
  useLayoutEffect(() => {
    if (!listOpen || !detailCategory) return;
    const key = `${queue}:${detailCategory.id}`;
    setExpandedCategories((previous) => previous.has(key) ? previous : new Set(Array.from(previous).concat(key)));
  }, [listOpen, queue, detailCategory]);

  useEffect(() => {
    if (flowSelectionRef.current !== undefined) {
      if (flowSelectionRef.current !== selectedProblemId) return;
      flowSelectionRef.current = undefined;
    }
    if (pendingNavigationRef.current || restoreTarget) return;
    if (detailProblemId && detailProblemId !== selectedProblemId) onSelectProblem(detailProblemId, { keepLayout: true });
  }, [detailProblemId, selectedProblemId, restoreTarget, onSelectProblem]);

  // 换一条问题时解释从头读起。
  useEffect(() => {
    if (readingRef.current) readingRef.current.scrollTop = 0;
  }, [detailProblemId]);

  // 列表展开时把当前条目滚进视野并聚焦，↑/↓ 可直接接着切换。
  useEffect(() => {
    // 类别展开前行仍是 hidden，必须在展开后的渲染再聚焦，否则键盘导航失效。
    if (!listOpen || !detailCategoryExpanded) return;
    const list = listRef.current;
    const card = list && detailProblemId ? findCard(list, detailProblemId) : undefined;
    if (list && card) {
      keepCardInView(list, card);
      card.focus({ preventScroll: true });
    }
  }, [listOpen, detailProblemId, detailCategoryExpanded]);

  const moveSelection = (delta: number) => {
    const next = orderedVisibleProblems[detailIndex + delta];
    if (!next) return;
    onSelectProblem(next.id);
  };

  const selectFromList = (problemId: string) => {
    onSelectProblem(problemId);
    setListOpen(false);
  };

  const handleAction = async (problem: Problem, action: IssueWorkflowAction, ignoreReason?: IgnoreReason) => {
    if (action === "ignore" && !ignoreReason) {
      setListOpen(false);
      setIgnoreTarget(problem);
      return;
    }
    if (effectiveQueueView === "pending" && (action === "confirm" || action === "ignore")) {
      const index = orderedVisibleProblems.findIndex((item) => item.id === problem.id);
      const remaining = orderedVisibleProblems.filter((item) => item.id !== problem.id);
      const next = remaining[Math.min(Math.max(0, index), remaining.length - 1)];
      pendingNavigationRef.current = {
        issueId: problem.id, status: action === "confirm" ? "confirmed" : "no_issue",
        nextId: next?.id ?? null, viewKey,
      };
    }
    const saved = await onAction(problem, action, ignoreReason);
    if (!saved && pendingNavigationRef.current?.issueId === problem.id) pendingNavigationRef.current = null;
    if (saved && action === "ignore") setIgnoreTarget(null);
    if (saved && action === "restore") {
      setStatusFilter("all");
      setRestoreTarget(problem.id);
    }
  };

  // 「未能自动核对」条目（R2.2 A3/IR04）：所有关闭性决定一律经过填写面板——
  // 范围确认（覆盖全部阻断点）与核对关系/依据是必填协议；系统定位只作
  // evidence_reference 附件自动带上，不再构成"一键绕过"。
  const handleObligation = async (problem: Problem, decision: ObligationDecision, note?: string) => {
    const item = obligationItems[problem.id];
    if (!item || !onObligationDecision) return;
    const autoEvidence = decision === "verified_ok" ? obligationAutoEvidence(item) : null;
    if (note === undefined && decision !== "pending") {
      setListOpen(false);
      setObligationPanel({ problem, decision, queue });
      return;
    }
    if (effectiveQueueView === "pending" && decision !== "pending") {
      const index = orderedVisibleProblems.findIndex((entry) => entry.id === problem.id);
      const remaining = orderedVisibleProblems.filter((entry) => entry.id !== problem.id);
      const next = remaining[Math.min(Math.max(0, index), remaining.length - 1)];
      pendingNavigationRef.current = { issueId: problem.id, status: decision, nextId: next?.id ?? null, viewKey };
    }
    setObligationSubmitting(true);
    const saved = await onObligationDecision(problem, decision, note ?? "", autoEvidence ?? "", true);
    setObligationSubmitting(false);
    if (!saved && pendingNavigationRef.current?.issueId === problem.id) pendingNavigationRef.current = null;
    if (saved) setObligationPanel(null);
    if (saved && decision === "pending") {
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
  const countOf = (kind: ReviewFilter) => kind === "all" ? sourceProblems.length
    : kind === "degraded" ? sourceProblems.filter((problem) => problem.evidenceStatus === EVIDENCE_STATUS_DEGRADED).length
      : sourceProblems.filter((problem) => problemReviewKind(problem) === kind && problem.evidenceStatus !== EVIDENCE_STATUS_DEGRADED).length;
  const categoryOptions = (["all", "issue", "obligation", "manual_review", "degraded", "tip", "review_notice"] as ReviewFilter[])
    .map((id) => [id, countOf(id)] as [ReviewFilter, number])
    .filter(([id, count]) => id === "all" || id === reviewFilter || count > 0);
  const emptyText = reviewFilter !== "all" ? "当前分类与来源筛选下没有检查结果。" : sourceView === "ai" && sourceFindings?.enabled
    ? sourceFindings.aiState === "succeeded" && sourceFindings.aiFindingCount === 0
      ? "大模型检查已完成，但系统没有收到可展示的大模型发现。"
      : sourceFindings.aiFindingCount > 0
        ? "当前队列没有可展示的大模型结果。"
        : sourceFindings.aiState === "running" || sourceFindings.aiState === "processing"
          ? "大模型仍在检查，完成后这里会显示大模型发现的问题。"
          : sourceFindings.aiState === "failed" || sourceFindings.aiState === "fallback"
            ? "大模型检查未成功完成，请查看检查阶段记录。"
            : "当前没有大模型发现的问题。"
    : sourceView === "common"
      ? "当前队列没有建立明确关系的共同发现。"
      : sourceView === "rule"
        ? "当前队列没有本地规则发现的问题。"
      : sourceView === "other"
        ? "当前队列没有其他系统复核事项。"
        : queue === "required" ? "当前没有必须处理的问题。" : "当前没有参考条目。";

  // 大模型按钮即使为 0 也保留：它带着 AI 执行状态的说明；其余为 0 的来源不占位。
  const sourceOptions: Array<[SourceView, number]> = sourceFindings?.enabled
    ? (["all", "rule", "ai", "common", "other"] as SourceView[])
      .map((id) => [id, sourceBuckets[id].length] as [SourceView, number])
      .filter(([id, count]) => id === "all" || id === "ai" || id === sourceView || count > 0)
    : [];
  const activeFilterLabels = [
    sourceView !== "all" ? `来源：${SOURCE_LABELS[sourceView]}` : null,
    reviewFilter !== "all" ? `类型：${REVIEW_FILTER_LABELS[reviewFilter]}` : null,
    effectiveQueueView === "all" && statusFilter !== "all" ? `状态：${STATUS_FILTER_LABELS[statusFilter]}` : null,
  ].filter((label): label is string => label !== null);
  const chipClass = (active: boolean) => `rounded-md border px-2 py-0.5 text-xs transition-colors ${
    active ? "border-primary-300 bg-primary-50 text-primary-700" : "border-border bg-white text-slate-600 hover:bg-surface-100"
  }`;
  const scopeLabel = effectiveQueueView === "pending" ? "待处理" : "全部";
  const noticeText = lastDecision
    ? `${lastDecision.status === "confirmed" ? "已确认" : lastDecision.status === "no_issue" ? "已忽略" : "已恢复为待处理"}：${lastDecision.problem.title}`
    : "";

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="gbc-review-issues-tab" data-review-queue={queue}>
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5" data-testid="gbc-review-issue-toolbar">
        <button
          type="button"
          onClick={() => { setIgnoreTarget(null); setListOpen(!listOpen); }}
          aria-expanded={listOpen}
          data-testid="gbc-review-toggle-list"
          className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${
            listOpen ? "border-primary-300 bg-primary-50 text-primary-700" : "border-border text-slate-700 hover:bg-surface-100"
          }`}
        >
          <ListFilter className="h-3.5 w-3.5" aria-hidden="true" />分类列表{activeFilterLabels.length > 0 ? ` · 筛选 ${activeFilterLabels.length}` : ""}
        </button>
        <div className="flex items-center gap-0.5 text-xs text-slate-500">
          <button type="button" onClick={() => moveSelection(-1)} disabled={!detailProblem || detailIndex <= 0} data-testid="gbc-review-issue-prev"
            aria-label="上一条" className="rounded p-1 text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <span className="min-w-[3em] whitespace-nowrap text-center tabular-nums" aria-live="polite" data-testid="gbc-review-issue-position"
            aria-label={detailProblem ? `${scopeLabel}第 ${detailIndex + 1} 条，共 ${orderedVisibleProblems.length} 条` : "无条目"}>
            {scopeLabel} {detailProblem ? `${detailIndex + 1} / ${orderedVisibleProblems.length}` : "0 / 0"}
          </span>
          <button type="button" onClick={() => moveSelection(1)} disabled={!detailProblem || detailIndex >= orderedVisibleProblems.length - 1} data-testid="gbc-review-issue-next"
            aria-label="下一条" className="rounded p-1 text-slate-600 hover:bg-surface-100 disabled:cursor-not-allowed disabled:opacity-40">
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <button
          type="button"
          className={`ml-auto inline-flex items-center gap-0.5 rounded-md px-1.5 py-1 text-xs hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${textSize === "large" ? "bg-primary-50 text-primary-700" : "text-slate-600"}`}
          onClick={onToggleTextSize}
          aria-pressed={textSize === "large"}
          aria-label={textSize === "large" ? "阅读字号：大字号，点击改为标准" : "阅读字号：标准，点击改为大字号"}
          title={textSize === "large" ? "阅读字号：大字号" : "阅读字号：标准"}
          data-testid="gbc-review-text-size"
        >
          <Type className="h-3.5 w-3.5" aria-hidden="true" />{textSize === "large" ? "大字" : "字号"}
        </button>
        <button
          type="button"
          onClick={onToggleFocus}
          aria-pressed={focusMode}
          aria-label={focusMode ? "还原布局" : "放大解释（原文栏收窄但保留）"}
          title={focusMode ? "还原布局" : "放大解释"}
          data-testid="gbc-review-focus-toggle"
          className={`inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${focusMode ? "bg-primary-50" : ""}`}
        >
          {focusMode ? <Minimize2 className="h-3.5 w-3.5" aria-hidden="true" /> : <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />}
          {focusMode ? "还原" : "放大"}
        </button>
      </div>

      <div className="relative min-h-0 min-w-0 flex-1" data-testid="gbc-review-issue-detail-pane">
        <div ref={readingRef} className="h-full overflow-y-auto" style={{ fontSize: REVIEW_TEXT_SIZE_PX[textSize] }} data-text-size={textSize}>
          {priorDecisions.length > 0 ? <details className="border-b border-border px-4 py-3 text-[0.85em]" data-testid="gbc-prior-decisions">
            <summary className="cursor-pointer font-medium text-slate-700">历史复核决定待重新对应（{priorDecisions.length}）</summary>
            <p className="mt-2 text-slate-500">历史意见已保留；本次依据变化或无法唯一对应，尚未沿用。</p>
            {priorDecisions.map((record) => {
              const note = parseDecisionNote(record.note);
              return <div key={record.issue_id} className="mt-3 border-t border-border pt-2">
                <p>{record.title ?? "历史复核事项"}{record.page ? ` · 第${record.page}页` : ""}</p>
                <p className="text-slate-500">原决定：{record.status === "no_issue" ? "已忽略" : record.status === "confirmed" ? "已确认" : record.status === "in_package" ? "已进整改包" : "待复核"}</p>
                {note.ignoreReason ? <p className="whitespace-pre-line">{note.ignoreReason.label}{note.ignoreReason.detail ? `：${note.ignoreReason.detail}` : ""}</p> : null}
                {note.userNote ? <p className="whitespace-pre-line">{note.userNote}</p> : null}
              </div>;
            })}
          </details> : null}
          {detailCategory && detailCategoryGroup ? (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border bg-surface-50 px-4 py-2 text-xs text-slate-600" data-testid="gbc-review-current-category">
              <button type="button" onClick={() => setListOpen(true)} className="font-medium text-primary-700 hover:underline">{detailCategory.label}</button>
              <span>本类第 {detailCategoryGroup.items.findIndex((item) => item.id === detailProblemId) + 1}/{detailCategoryGroup.items.length} 项</span>
              {relatedQueueProblems.length > 0 && onOpenRelatedQueue ? (
                <button type="button" className="ml-auto text-primary-700 hover:underline" data-testid="gbc-review-related-category"
                  onClick={() => {
                    clearFilters();
                    setQueueView("all");
                    onOpenRelatedQueue(queue === "required" ? "reference" : "required", relatedQueueProblems[0].id);
                  }}
                >同类{queue === "required" ? "参考" : "必须处理"} {relatedQueueProblems.length} 项</button>
              ) : null}
            </div>
          ) : null}
          {detailProblem && isObligationProblem(detailProblem) && obligationItems[detailProblem.id] ? (
            <ObligationCard
              key={detailProblem.id}
              problem={detailProblem}
              item={obligationItems[detailProblem.id]}
              allItems={Object.values(obligationItems)}
              workflowStatus={workflowStatusByIssueId[detailProblem.id] ?? null}
              selectedEvidenceRefKey={detailProblem.id === selectedProblemId ? selectedEvidenceRefKey : null}
              onSelectEvidenceRef={(refKey) => onSelectEvidenceRef(detailProblem, refKey)}
              onOpenScope={onOpenScope}
            />
          ) : detailProblem ? (
            <IssueCard
              key={detailProblem.id}
              problem={detailProblem}
              workflowStatus={workflowStatusByIssueId[detailProblem.id] ?? null}
              workflowNote={workflowNotes[detailProblem.id]}
              workflowInherited={workflowInheritedByIssueId[detailProblem.id]}
              selectedEvidenceRefKey={detailProblem.id === selectedProblemId ? selectedEvidenceRefKey : null}
              onSelectEvidenceRef={(refKey) => onSelectEvidenceRef(detailProblem, refKey)}
              cascadedChecks={cascadedChecks[detailProblem.id]}
            />
          ) : (
            <div className="p-6 text-center text-sm text-slate-500" data-testid="gbc-review-issues-empty">
              <p>{effectiveQueueView === "pending" && classifiedProblems.length > 0 ? "当前队列已无待处理项。" : effectiveQueueView === "all" && statusFilter !== "all" ? "当前筛选下没有该处理状态的条目。" : emptyText}</p>
              {effectiveQueueView === "pending" && classifiedProblems.length > 0 ? <button type="button" onClick={() => { setQueueView("all"); setStatusFilter("all"); }} className="mt-3 text-primary-700 hover:underline" data-testid="gbc-review-view-all">查看全部条目</button> : null}
            </div>
          )}
        </div>

        {listOpen ? (
          <div className="absolute inset-0 z-20 flex flex-col bg-white" data-testid="gbc-review-list-panel"
            onKeyDown={(event) => { if (event.key === "Escape") setListOpen(false); }}>
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2">
              <strong className="text-sm font-semibold text-slate-900">{queue === "required" ? "必须处理的问题" : "参考发现"}</strong>
              <span className="text-xs text-slate-500" data-testid="gbc-review-category-count">{categoryGroups.length} 类 · {visibleProblems.length} 项</span>
              <div className="flex items-center gap-0.5 rounded-md bg-surface-100 p-0.5" aria-label="按处理进度查看">
                {([["pending", "待处理", pendingProblems.length], ["all", "全部", classifiedProblems.length]] as Array<[QueueView, string, number]>).map(([id, label, count]) => (
                  <button key={id} type="button" aria-pressed={effectiveQueueView === id}
                    disabled={readOnly && id === "pending"}
                    onClick={() => { setQueueView(id); setStatusFilter("all"); }}
                    data-testid={`gbc-review-scope-${id}`}
                    className={`rounded px-2 py-0.5 text-xs disabled:cursor-not-allowed disabled:opacity-50 ${effectiveQueueView === id ? "bg-primary-600 font-medium text-white" : "text-slate-600 hover:bg-white"}`}
                  >{label} <span className="tabular-nums">{count}</span></button>
                ))}
              </div>
              <span className="text-[11px] text-slate-500" data-testid="gbc-review-issues-count">正式问题（{verifiedIssueCount}）</span>
              <button type="button" onClick={() => setListOpen(false)} aria-label="关闭问题列表" data-testid="gbc-review-close-list"
                className="ml-auto rounded p-1 text-slate-500 hover:bg-surface-100">
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
            <div className="shrink-0 space-y-1.5 border-b border-border bg-surface-50 px-4 py-2" data-testid="gbc-review-filter-panel">
              {hasFilters ? (
                <p className="text-xs text-primary-700">
                  {activeFilterLabels.join(" · ") || "已筛选"}
                  <button type="button" onClick={clearFilters} data-testid="gbc-review-clear-filters" className="ml-2 font-medium hover:underline">清除筛选</button>
                </p>
              ) : null}
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
              {categoryOptions.length > 1 ? (
                <div className="flex flex-wrap items-center gap-1.5" aria-label="按结果分类筛选">
                  <span className="mr-1 w-14 text-xs text-slate-500">结果类型</span>
                  {categoryOptions.map(([id, count]) => (
                    <button key={id} type="button" aria-pressed={reviewFilter === id} onClick={() => setReviewFilter(id)}
                      data-testid={`gbc-review-category-${id}`} className={chipClass(reviewFilter === id)}
                    >{REVIEW_FILTER_LABELS[id]} <span className="opacity-75">{count}</span></button>
                  ))}
                </div>
              ) : null}
              {sourceFindings?.enabled ? (
                <button type="button" onClick={() => setShowSourceDetails((value) => !value)} aria-expanded={showSourceDetails}
                  data-testid="gbc-review-toggle-source-details" className="text-xs text-slate-500 hover:text-slate-700"
                >来源与分类说明 {showSourceDetails ? "⌃" : "⌄"}</button>
              ) : null}
              {sourceFindings?.enabled && showSourceDetails ? (
                <div className="rounded-md bg-white px-3 py-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600" data-testid="gbc-review-source-summary">
                    <span className="text-slate-500">本次检查（必须处理与参考合计）</span>
                    <span>大模型：{aiStateLabel(sourceFindings.aiState)} · {sourceFindings.aiFindingCount} 条发现</span>
                    {sourceFindings.aiModel ? <span>模型：{sourceFindings.aiModel}</span> : null}
                    {sourceFindings.aiCoverage ? (
                      <span data-testid="gbc-review-ai-coverage">
                        检查覆盖：{aiCoverageLabel(sourceFindings.aiCoverage.status)}
                        （有效完成 {sourceFindings.aiCoverage.succeeded_calls
                          ?? Math.max(0, (sourceFindings.aiCoverage.completed_calls ?? 0) - (sourceFindings.aiCoverage.failed_calls ?? 0))}
                        /{sourceFindings.aiCoverage.planned_calls ?? 0}
                        {(sourceFindings.aiCoverage.failed_calls ?? 0) > 0 ? `；失败 ${sourceFindings.aiCoverage.failed_calls}` : ""}
                        {(sourceFindings.aiCoverage.retried_calls ?? 0) > 0 ? `；重试 ${sourceFindings.aiCoverage.retried_calls} 个单元` : ""}
                        {(sourceFindings.aiCoverage.partial_calls ?? 0) > 0 ? `；部分 ${sourceFindings.aiCoverage.partial_calls}` : ""}
                        {(sourceFindings.aiCoverage.invalid_calls ?? 0) > 0 ? `；非法响应 ${sourceFindings.aiCoverage.invalid_calls}` : ""}
                        {(sourceFindings.aiCoverage.truncated_calls ?? 0) > 0 ? `；截断 ${sourceFindings.aiCoverage.truncated_calls}` : ""}
                        ）
                      </span>
                    ) : null}
                    <span>本地规则：{sourceFindings.ruleFindingCount} 条发现</span>
                    <span>其他复核项：{sourceFindings.other.length} 条</span>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">上方来源按钮只计当前页签（必须处理或参考）的条目，点开即是该数目。本地规则和大模型各自展示完整结果，包含共同发现；两侧数量不能直接相加。共同发现按明确的检查关系，或原件中同一处已核验的重复词匹配；原文相同不代表 AI 结论已经确认。</div>
                  <div className="mt-1 text-xs text-slate-500">必须处理：正式问题与结构化识别待复核项，计入完成条件。参考：待核实、证据不足、提示，供查阅，不计入完成条件。</div>
                </div>
              ) : null}
              {sourceView === "ai" && sourceFindings?.aiState === "succeeded" && sourceFindings.aiFindingCount === 0 ? (
                <div className="rounded-md border border-info-200 bg-info-50 px-2.5 py-2 text-[11px] leading-relaxed text-info-700" data-testid="gbc-review-ai-empty-completed">
                  大模型检查已完成，但没有收到可展示的问题。这个状态表示本次 AI 结果为 0 条，不代表本地规则没有发现问题。
                </div>
              ) : null}
              {sourceFindings?.aiState === "degraded" ? (
                <div className="rounded-md border border-warning-200 bg-warning-50 px-2.5 py-2 text-[11px] leading-relaxed text-warning-700" data-testid="gbc-review-ai-degraded">
                  大模型检查未完整完成。本次保存了 {sourceFindings.aiFindingCount} 条大模型发现；请在“大模型”中查看，并结合证据状态复核。
                  {sourceFindings.aiCoverage?.uncovered_ranges?.length ? (
                    <span className="mt-1 block" data-testid="gbc-review-ai-uncovered">
                      未覆盖页：
                      {sourceFindings.aiCoverage.uncovered_ranges
                        .flatMap((range) => range.pages ?? [])
                        .filter((page, index, all) => all.indexOf(page) === index)
                        .join("、") || "范围未能映射到页码"}
                      （原因：窗口上限）。已检查范围不等于保证无遗漏。
                    </span>
                  ) : null}
                  {(sourceFindings.aiCoverage?.invalid_calls ?? 0) + (sourceFindings.aiCoverage?.truncated_calls ?? 0) > 0 ? (
                    <span className="mt-1 block">部分模型响应未通过校验或被截断；合法候选已保留，非法条目已拒绝，未计为“无问题”。</span>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div
              ref={listRef}
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                event.preventDefault();
                const next = orderedVisibleProblems[detailIndex + (event.key === "ArrowDown" ? 1 : -1)];
                if (!next) return;
                onSelectProblem(next.id);
              }}
              className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
              data-testid="gbc-review-issues-list"
            >
              {visibleProblems.length === 0 ? (
                <p className="px-2 py-6 text-center text-sm text-slate-500">{effectiveQueueView === "pending" && classifiedProblems.length > 0 ? "当前队列已无待处理项。" : emptyText}</p>
              ) : categoryGroups.map(({ id, label, description, items }) => {
                const key = `${queue}:${id}`;
                const expanded = expandedCategories.has(key);
                return (
                  <section key={id} className="mb-2 rounded-md border border-border" data-testid={`gbc-review-business-category-${id}`}>
                    <button type="button" aria-expanded={expanded} aria-controls={`review-category-${queue}-${id}`}
                      data-testid={`gbc-review-category-toggle-${id}`}
                      className="sticky top-0 z-10 flex w-full items-center gap-2 rounded-t-md bg-surface-50 px-3 py-2 text-left text-xs text-slate-700"
                      onClick={() => setExpandedCategories((previous) => {
                        const next = new Set(previous);
                        if (next.has(key)) next.delete(key); else next.add(key);
                        return next;
                      })}>
                      <span className="min-w-0 flex-1"><strong className="block font-semibold">{label}</strong><span className="mt-0.5 block text-[11px] text-slate-500">{description}</span></span>
                      <span className="shrink-0 tabular-nums">{items.length} 项</span><span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span>
                    </button>
                    <div id={`review-category-${queue}-${id}`} hidden={!expanded} className="space-y-0.5 px-1 pb-1">
                      {items.map((problem) => (
                        <IssueRow
                          key={problem.id}
                          problem={problem}
                          isActive={problem.id === detailProblemId}
                          workflowStatus={workflowStatusByIssueId[problem.id] ?? null}
                          onSelect={() => selectFromList(problem.id)}
                        />
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
            <div className="shrink-0 border-t border-border px-4 py-2 text-[11px] text-slate-500">
              {queue === "required" ? "证据充分的问题可随整份清单确认；材料歧义需判断，系统未完成的检查需修复或重查。" : "参考项不影响完成复核；可按需确认、忽略或补充意见。"}
              <span className="ml-1 text-slate-400">同类集中展示，各项分别记录结论；↑ ↓ 切换，Esc 关闭。</span>
            </div>
          </div>
        ) : null}

        {ignoreTarget ? (
          <IgnoreReasonPanel
            key={ignoreTarget.id}
            problemTitle={ignoreTarget.title}
            isSubmitting={submittingIssueId !== null}
            onSubmit={(reason) => void handleAction(ignoreTarget, "ignore", reason)}
            onCancel={() => setIgnoreTarget(null)}
          />
        ) : null}

        {obligationPanelTarget ? (
          <ObligationDecisionPanel
            key={`${obligationPanelTarget.problem.id}:${obligationPanelTarget.decision}`}
            decision={obligationPanelTarget.decision}
            checkName={obligationItems[obligationPanelTarget.problem.id]?.title ?? obligationPanelTarget.problem.title}
            defaultNote={readableObligationReason(obligationItems[obligationPanelTarget.problem.id]?.detail)}
            item={obligationItems[obligationPanelTarget.problem.id] ?? null}
            isSubmitting={obligationSubmitting}
            onSubmit={(note) => void handleObligation(obligationPanelTarget.problem, obligationPanelTarget.decision, note)}
            onCancel={() => setObligationPanel(null)}
          />
        ) : null}
      </div>

      {detailProblem || lastDecision || readOnlyHint ? (
        <IssueDecisionFooter
          problem={detailProblem}
          workflowStatus={detailProblem ? workflowStatusByIssueId[detailProblem.id] ?? null : null}
          showActions={Boolean(detailProblem) && !readOnly && !obligationPanelTarget && obligationItems[detailProblem?.id ?? ""]?.handling_kind !== "system_recheck" && (sourceView === "all" || (detailProblem !== null && baseProblemIds.has(detailProblem.id)))}
          readOnlyHint={readOnlyHint}
          savedAtText={savedAtText}
          isSubmitting={submittingIssueId !== null || obligationSubmitting}
          onAction={(action) => { if (detailProblem) void handleAction(detailProblem, action); }}
          onObligationDecision={(decision) => { if (detailProblem) void handleObligation(detailProblem, decision); }}
          notice={lastDecision ? { text: noticeText, canUndo: !readOnly, onUndo: () => void handleUndo() } : null}
        />
      ) : null}
    </div>
  );
}

export default IssuesTab;
