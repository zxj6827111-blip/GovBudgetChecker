/**
 * ReviewWorkbenchPage：Task 6，1:1 还原 `03-review-workbench.png`——把现有
 * "点问题弹模态框看单页"改成可调整宽度的左右对照工作区。
 *
 * 入口约定：`/review?job={job_id}`（与既有 `/queue?job={job_id}` 同构的查询参数
 * 命名）；一级 /review 由 ReviewEntryPage 显示材料清单，用户选择后才进入详情。
 *
 * 左 PDF 视图（缩略图按需展开） | 右 问题/元数据/阶段记录页签。
 * 原文栏从窗口顶端通到底：工作区顶栏、文件信息、操作按钮、状态条和底部统计
 * 都画在右栏（顶栏经 EmbeddedWorkspaceTopbar 由本页绘制），尽量把高度留给原文。
 *
 * 数据来源：
 * - `/api/jobs/{job_id}`：job detail（复用既有 toUiProblems/toUiTask 提取问题
 *   与元数据，不重新发明提取逻辑）；
 * - `/api/workflow`：问题工作流状态（唯一路径，见 workflow/route.ts 顶部注释）；
 * - `/api/reviews?job_uuid=...`：**可持久化的复核生命周期**（WP3-A）——
 *   由服务端把 job 解析到材料槽位，页面据此判定能否复核、为什么不能；
 * - `/api/files/{job_id}/preview`：缩略图与中栏大图（已有接口，无需新增后端）。
 *
 * 「完成复核」的真实语义（WP3-A 起）
 * ---------------------------------
 * 此前这个按钮只在 `pending == 0` 时 `router.push("/queue")`，**没有任何后端写入**
 * ——刷新之后系统并不知道这份材料被复核过。现在它调用
 * `POST /api/reviews/{slot_id}/complete`，由服务端重算全部门禁：
 * 版本是否还是那一版、分析代际是否还是那一代、问题是否都有人表过态、
 * 身份与口径是否已解决、检查覆盖是否可信且没有阻塞义务。
 *
 * 三条前端纪律：
 * 1. 按钮的 disabled 只是**即时提示**（少让用户白跑一趟），能不能完成永远由
 *    服务端决定——它还知道版本/代际/覆盖这些前端未必知道的事实；
 * 2. 服务端返回 `blockers` 时必须逐条显示业务原因，不能只显示"HTTP 409"；
 * 3. 浏览不创建会话，开始与完成均由用户明确点击。
 */
"use client";
import AnalysisCheckProgress from "@/components/AnalysisCheckProgress";
import { displayedAnalysisRevision, reportDownloadUrl } from "@/lib/reportVersion";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2, PanelsTopLeft } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import { EmbeddedWorkspaceTopbar } from "@/components/workspace/EmbeddedWorkspaceTopbar";
import { resolvePollingDecision } from "@/lib/jobPolling";
import type { Problem } from "@/lib/mock";
import { describeBlockers, invalidationReasonLabel } from "@/lib/reviewLifecyclePresentation";
import type { JobDetailRecord, StructuredIngestRecord } from "@/lib/uiAdapters";
import { invalidateReviewWorklist } from "@/lib/reviewWorklistEvents";
import { readReviewJson, invalidateReviewReads } from "@/lib/reviewReadRequests";
import { safeReviewReturnPath } from "./reviewListAdapters";
import { ReviewDetailNavigation } from "./ReviewDetailNavigation";
import { isUiTaskFinished, normalizeUiTaskStatus, selectStructuredForJob, toUiProblems, toUiSourceProblems } from "@/lib/uiAdapters";

import { CorrectionsTab, type CorrectionFocus } from "./CorrectionsTab";
import { IssueNoteDialog } from "./IssueNoteDialog";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssuesTab } from "./IssuesTab";
import type { IssueDecisionNotice, SourceFindingViews } from "./IssuesTab";
import { MetadataTab } from "./MetadataTab";
import { ObligationsTab } from "./ObligationsTab";
import { PdfViewerPane } from "./PdfViewerPane";
import {
  REVIEW_CONTEXT_UNAVAILABLE_MESSAGE,
  describeCompletedReview,
  formatReviewMoment,
  latestCompletedSession,
  latestInvalidatedSession,
  canDecideObligations,
  parseReviewErrorDetail,
  pendingObligationCount,
  resolveCompleteButtonState,
  resolveReviewBadge,
  canStartReview,
  toReviewContextState,
  type ObligationReviewItemRecord,
  type ReviewByJobDataRecord,
  type ReviewContextState,
  type ReviewErrorDetail,
} from "./reviewLifecycleAdapters";
import {
  computeWorkflowStatusCounts,
  classifyFindingSources,
  extractTotalPageCount,
  resolveProblemTargetPage,
  resolveWorkbenchHeaderBadge,
  type WorkflowIssueRecord,
} from "./reviewWorkbenchAdapters";
import { StageHistoryTab } from "./StageHistoryTab";
import { ThumbnailRail } from "./ThumbnailRail";
import { useJobPolling } from "../workspace/useJobPolling";

type RightTabId = "issues" | "corrections" | "metadata" | "stages" | "obligations";

interface WorkflowStateResponse {
  issues?: Record<string, { issue_id: string; job_id: string; status: string; note?: string | null }>;
}

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    return await readReviewJson<T>(url);
  } catch {
    return fallback;
  }
}

function formatSavedAtTime(date: Date): string {
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

/** 加载、未找到、未分析完成等状态也要带顶栏：复核详情页的顶栏由页面自己绘制。 */
function ReviewStateFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <EmbeddedWorkspaceTopbar />
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/** 原文栏默认占 45%，问题区拿到更宽的 55%；拖动后的宽度按浏览器记住。 */
const DOCUMENT_SHARE_KEY = "gbc-review-document-share";
const DEFAULT_DOCUMENT_SHARE = 45;
/** 状态条里阻塞原因超过这个条数时先收起，避免把问题区再挤回去。 */
const COLLAPSED_BLOCKER_COUNT = 4;

function clampDocumentShare(value: number): number {
  return Math.max(35, Math.min(65, Math.round(value)));
}

function persistDocumentShare(value: number) {
  try {
    window.localStorage.setItem(DOCUMENT_SHARE_KEY, String(value));
  } catch {
    // 记不住宽度时仍按本次拖动显示。
  }
}

export function ReviewWorkbenchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";
  const returnTo = safeReviewReturnPath(searchParams.get("returnTo"));

  const [detail, setDetail] = useState<JobDetailRecord | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [activeTab, setActiveTab] = useState<RightTabId>("issues");
  const [showThumbnails, setShowThumbnails] = useState(false);
  const [issuesExpanded, setIssuesExpanded] = useState(false);
  const [documentShare, setDocumentShare] = useState(DEFAULT_DOCUMENT_SHARE);
  const [showAllBlockers, setShowAllBlockers] = useState(false);
  const splitPaneRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(DOCUMENT_SHARE_KEY));
      if (saved > 0) setDocumentShare(clampDocumentShare(saved));
    } catch {
      // 读不到偏好时使用默认宽度。
    }
  }, []);
  const [selectedProblemId, setSelectedProblemId] = useState<string | null>(null);
  const [selectedEvidenceRefKey, setSelectedEvidenceRefKey] = useState<string | null>(null);
  const [workflowIssues, setWorkflowIssues] = useState<Record<string, WorkflowIssueRecord>>({});
  const [workflowNotes, setWorkflowNotes] = useState<Record<string, string>>({});
  const [submittingIssueId, setSubmittingIssueId] = useState<string | null>(null);
  const [lastIssueDecision, setLastIssueDecision] = useState<IssueDecisionNotice | null>(null);
  const workflowMutationRef = useRef(false);
  const activeJobIdRef = useRef(jobId);
  activeJobIdRef.current = jobId;
  const [noteDialogProblem, setNoteDialogProblem] = useState<Problem | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [isReanalyzing, setIsReanalyzing] = useState(false);
  /** 「读取校正」页签选中的原页位置；只在该页签激活时接管左侧高亮。 */
  const [correctionFocus, setCorrectionFocus] = useState<CorrectionFocus | null>(null);
  /** 复核生命周期上下文（WP3-A）：由服务端把 job 解析到材料槽位后返回。 */
  const [reviewContext, setReviewContext] = useState<ReviewContextState>({ kind: "loading" });
  /** 正在提交复核写动作（开始/完成/重开）。 */
  const [reviewBusy, setReviewBusy] = useState(false);
  /** 服务端拒绝完成时的业务原因（逐条显示，不是一句"HTTP 409"）。 */
  const [reviewBlockers, setReviewBlockers] = useState<ReviewErrorDetail | null>(null);
  /** 复核写动作失败的非门禁原因（网络/权限/服务不可用）。 */
  const [reviewNotice, setReviewNotice] = useState<string | null>(null);
  /** 正在提交人工补核的义务 id（WP3-B，防连点）。 */
  const [obligationSubmittingId, setObligationSubmittingId] = useState<string | null>(null);
  const loadSeqRef = useRef(0);
  const reviewLoadSeqRef = useRef(0);
  const workflowLoadSeqRef = useRef(0);

  /** 最新 detail 的旁路引用：轮询决策每次续排时实时读取。 */
  const detailRef = useRef<JobDetailRecord | null>(null);
  detailRef.current = detail;

  /**
   * 装载复核生命周期上下文（WP3-A）。
   *
   * 必须由服务端把 job 解析到槽位：页面只知道 job_id，而"这次运行属于哪条应收
   * 材料"只能靠 `structured_ingest.document_version_id` 这条精确链路确定。
   * 解析不出来（旧任务没有精确版本链路）时**不是错误**：页面显示"仅供查看历史
   * 审核内容"并关闭完成入口（§七十三）。
   */
  const loadReviewContext = useCallback(async (): Promise<ReviewContextState> => {
    if (!jobId) {
      return { kind: "unavailable", message: REVIEW_CONTEXT_UNAVAILABLE_MESSAGE };
    }
    const seq = ++reviewLoadSeqRef.current;
    let payload: ReviewByJobDataRecord | null = null;
    try {
      const body = await readReviewJson<{ data?: ReviewByJobDataRecord }>(`/api/reviews?job_uuid=${encodeURIComponent(jobId)}`);
      payload = body.data ?? null;
    } catch {
      payload = null;
    }
    const next = toReviewContextState(payload);
    if (seq === reviewLoadSeqRef.current) setReviewContext(next);
    return next;
  }, [jobId]);

  /**
   * 提交一次复核写动作（start / complete / reopen）。
   *
   * 返回值把"是否成功 + 业务错误体"一起交给调用方：门禁拒绝与网络失败要分开处理，
   * 前者要逐条展示 blockers，后者只能说"服务暂时不可用"。
   */
  const submitReviewAction = useCallback(
    async (
      slotId: string,
      action: "start" | "complete" | "reopen",
    ): Promise<{ ok: boolean; detail: ReviewErrorDetail | null }> => {
      setReviewBusy(true);
      setReviewNotice(null);
      try {
        const response = await fetch(`/api/reviews/${encodeURIComponent(slotId)}/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_uuid: jobId }),
        });
        if (response.ok) {
          loadSeqRef.current += 1; reviewLoadSeqRef.current += 1; workflowLoadSeqRef.current += 1;
          invalidateReviewReads(jobId);
          setReviewBlockers(null);
          invalidateReviewWorklist();
          return { ok: true, detail: null };
        }
        let body: unknown = null;
        try {
          body = await response.json();
        } catch {
          body = null;
        }
        const parsed = parseReviewErrorDetail(body);
        if (parsed) {
          // 门禁拒绝：逐条展示业务原因。只显示"HTTP 409"等于把唯一有用的信息丢掉。
          // 但 blockers 为空的 409（如问题集合在工作流上已变化）没有可逐条
          // 渲染的条目——业务消息必须落到通知条，不能静默。
          if (parsed.blockers && parsed.blockers.length > 0) {
            setReviewBlockers(parsed);
          } else {
            setReviewNotice(parsed.message);
          }
          return { ok: false, detail: parsed };
        }
        setReviewNotice(
          response.status === 403
            ? "没有权限对这条材料执行该操作。"
            : response.status === 404
              ? "这条材料的复核上下文已不存在。"
              : `操作未完成（HTTP ${response.status}）。`,
        );
        return { ok: false, detail: null };
      } catch {
        setReviewNotice("复核服务暂时不可用，请稍后重试。");
        return { ok: false, detail: null };
      } finally {
        setReviewBusy(false);
      }
    },
    [jobId],
  );

  const loadJobDetail = useCallback(
    async (options: { silent?: boolean } = {}) => {
      if (!jobId) {
        setLoading(false);
        return;
      }
      const seq = ++loadSeqRef.current;
      // 轮询触发的刷新必须静默：不重置 loading，避免已渲染的内容每 5 秒闪一次加载态。
      if (!options.silent) {
        setLoading(true);
      }
      // 生命周期与详情独立读取，原文和问题不必等待串行的上下文请求。
      const contextRead = loadReviewContext();
      const [jobDetail, structured] = await Promise.all([
        fetchJson<JobDetailRecord | null>(`/api/jobs/${encodeURIComponent(jobId)}`, null),
        fetchJson<StructuredIngestRecord>(`/api/jobs/${encodeURIComponent(jobId)}/structured-ingest`, {}),
      ]);
      if (seq !== loadSeqRef.current) {
        // 任务切换后旧请求才返回：丢弃过期结果，避免把上一个 job 的详情渲染到
        // 当前 job 的页面上。
        return;
      }
      if (!jobDetail) {
        setDetail(null);
        setProblems([]);
        setLoading(false);
        return;
      }
      const boundStructured = selectStructuredForJob(jobDetail, structured);
      const nextProblems = toUiProblems({ ...jobDetail, structured_ingest: boundStructured }).map((problem) => ({
        ...problem,
        jobId: jobDetail.job_id,
      }));
      // 同步更新旁路引用（理由同各列表页 fetcher 内注释：轮询决策先于重渲染执行）。
      detailRef.current = jobDetail;
      setDetail(jobDetail);
      setProblems(nextProblems);
      setLoading(false);

      await contextRead;
    },
    [jobId, loadReviewContext],
  );

  /**
   * 装载问题工作流状态（`/api/workflow` 是问题人工处理状态的**唯一**路径）。
   *
   * 只保留当前任务的记录：同一个 store 里混着全部任务的问题决定，
   * 不过滤会让另一份材料的"已确认"显示在本页上。
   */
  const loadWorkflow = useCallback(async () => {
    const seq = ++workflowLoadSeqRef.current;
    const payload = await fetchJson<WorkflowStateResponse | null>(`/api/workflow?job_id=${encodeURIComponent(jobId)}`, null);
    // 重取失败时保留已保存的决定，避免把短暂读取故障误当成“全部未处理”。
    if (seq !== workflowLoadSeqRef.current || !payload?.issues) return;
    const issues = payload.issues ?? {};
    const nextIssues: Record<string, WorkflowIssueRecord> = {};
    const nextNotes: Record<string, string> = {};
    for (const record of Object.values(issues)) {
      if (record.job_id !== jobId) {
        continue;
      }
      nextIssues[record.issue_id] = { issue_id: record.issue_id, status: record.status };
      if (record.note) {
        nextNotes[record.issue_id] = record.note;
      }
    }
    setWorkflowIssues(nextIssues);
    setWorkflowNotes(nextNotes);
  }, [jobId]);

  // 首次详情由轮询控制器负责，避免 effect 与 controller.start 重复发起读取。
  // 两者都要：只拉详情会让"已确认/已忽略"在刷新后归零（工作流状态是另一条接口），
  // 用户会以为自己的处理动作丢了。
  useEffect(() => {
    void loadWorkflow();
    return () => { loadSeqRef.current += 1; reviewLoadSeqRef.current += 1; workflowLoadSeqRef.current += 1; };
  }, [loadWorkflow]);

  // 修复 1：任务未分析完成时轮询详情（默认 5 秒），完成后任务从"尚未分析完成"
  // 的引导态自动翻转到可复核内容——此前只能手动刷新页面。
  // 任务已终态或拉不到详情时决策为 stop，不向 /api/jobs/{id} 继续打请求。
  useJobPolling({
    fetcher: useCallback(() => loadJobDetail({ silent: true }), [loadJobDetail]),
    decide: useCallback(() => {
      const current = detailRef.current;
      if (current && !isUiTaskFinished(normalizeUiTaskStatus(current.status))) {
        return resolvePollingDecision([current]);
      }
      return current ? { kind: "stop" as const } : resolvePollingDecision(null);
    }, []),
  });

  useEffect(() => {
    setCurrentPage(1);
    setSelectedProblemId(null);
    setSelectedEvidenceRefKey(null);
    setLastIssueDecision(null);
    setWorkflowIssues({});
    setWorkflowNotes({});
    // 切换任务时重置复核状态：否则上一条材料的 blockers 会挂在新材料的页面上。
    setReviewBlockers(null);
    setReviewNotice(null);
    setReviewContext({ kind: "loading" });
  }, [jobId]);

  const totalPages = extractTotalPageCount(detail);

  const workflowStatusByIssueId = useMemo(() => {
    const result: Record<string, string> = {};
    for (const [issueId, record] of Object.entries(workflowIssues)) {
      result[issueId] = record.status;
    }
    return result;
  }, [workflowIssues]);

  const statusCounts = useMemo(
    () => computeWorkflowStatusCounts(problems, workflowIssues),
    [problems, workflowIssues],
  );

  const sourceFindings = useMemo<SourceFindingViews | undefined>(() => {
    if (!detail || !detail.result) return undefined;
    const result = detail.result;
    const hasSourceBuckets = Array.isArray(result.ai_findings) || Array.isArray(result.rule_findings);
    if (!hasSourceBuckets) return undefined;

    const aiProblems = toUiSourceProblems(detail, "ai", { includeEvidencePreview: false });
    const ruleProblems = toUiSourceProblems(detail, "rule", { includeEvidencePreview: false });
    const aiById = new Map(aiProblems.map((problem) => [problem.id, problem]));
    const ruleById = new Map(ruleProblems.map((problem) => [problem.id, problem]));
    const classification = classifyFindingSources(result);
    const common: Problem[] = [];
    const pairedIds = new Set<string>();

    for (const relation of classification.commonRelations) {
      const aiItems = relation.aiIssueIds.flatMap((id) => aiById.has(id) ? [aiById.get(id)!] : []);
      const ruleItems = relation.ruleIssueIds.flatMap((id) => ruleById.has(id) ? [ruleById.get(id)!] : []);
      [...relation.aiIssueIds, ...relation.ruleIssueIds].forEach((id) => pairedIds.add(id));
      if (relation.status === "agreement" || relation.status === "same_occurrence") {
        const representative = ruleItems[0] ?? aiItems[0];
        if (representative) {
          common.push({
            ...representative,
            findingSources: ["ai", "rule"],
            sourceRelationLabel: relation.status === "same_occurrence" ? "同一处重复用词" : "两侧一致",
          });
        }
        continue;
      }

      const relationLabel = relation.status === "conflict"
        ? "两侧结论不一致"
        : relation.status === "ambiguous"
          ? "两侧均发现 · 关系待核实"
          : relation.status === "execution_error"
            ? "两侧均发现 · 对照失败"
            : "两侧均发现";
      for (const problem of [...aiItems, ...ruleItems]) {
        common.push({
          ...problem,
          findingSources: [problem.source === "ai" ? "ai" : "rule"],
          sourceRelationLabel: relationLabel,
        });
      }
    }

    const aiOnly = aiProblems
      .filter((problem) => classification.aiOnlyIssueIds.has(problem.id) && !pairedIds.has(problem.id))
      .map((problem) => ({ ...problem, findingSources: ["ai"] as Array<"ai" | "rule"> }));
    const ruleOnly = ruleProblems
      .filter((problem) => classification.ruleOnlyIssueIds.has(problem.id) && !pairedIds.has(problem.id))
      .map((problem) => ({ ...problem, findingSources: ["rule"] as Array<"ai" | "rule"> }));
    const sourceIds = new Set([...aiProblems, ...ruleProblems].map((problem) => problem.id));
    const other = problems.filter((problem) => !sourceIds.has(problem.id));
    const meta = result.meta && typeof result.meta === "object" && !Array.isArray(result.meta)
      ? result.meta as Record<string, unknown>
      : {};
    const aiExecution = meta.ai_execution && typeof meta.ai_execution === "object" && !Array.isArray(meta.ai_execution)
      ? meta.ai_execution as Record<string, unknown>
      : {};
    const aiFindingCount = Array.isArray(result.ai_findings) ? result.ai_findings.length : 0;
    const ruleFindingCount = Array.isArray(result.rule_findings) ? result.rule_findings.length : 0;

    return {
      enabled: true,
      common,
      ai: aiProblems,
      rule: ruleProblems,
      aiOnly,
      ruleOnly,
      other,
      aiFindingCount,
      ruleFindingCount,
      commonRelationCount: classification.commonRelations.length,
      aiState: String(aiExecution.state ?? meta.ai_status ?? "unknown"),
      aiModel: String(aiExecution.model ?? "").trim() || undefined,
    };
  }, [detail, problems]);

  const selectableProblems = useMemo(
    () => [
      ...problems,
      ...(sourceFindings?.ai ?? []),
      ...(sourceFindings?.rule ?? []),
      ...(sourceFindings?.common ?? []),
      ...(sourceFindings?.aiOnly ?? []),
      ...(sourceFindings?.ruleOnly ?? []),
      ...(sourceFindings?.other ?? []),
    ],
    [problems, sourceFindings],
  );

  const selectedProblem = useMemo(
    () => selectableProblems.find((problem) => problem.id === selectedProblemId) ?? null,
    [selectableProblems, selectedProblemId],
  );

  const highlightedProblem = useMemo(() => {
    if (!selectedProblem || !selectedEvidenceRefKey) return selectedProblem;
    const ref = selectedProblem.evidenceRefs?.find((item) => item.key === selectedEvidenceRefKey);
    return ref ? { ...selectedProblem, page: ref.page, bbox: ref.bbox } : selectedProblem;
  }, [selectedProblem, selectedEvidenceRefKey]);

  const pdfHighlight = useMemo<Problem | null>(() => {
    if (activeTab !== "corrections") return highlightedProblem;
    if (!correctionFocus) return null;
    return {
      id: "manual-correction-focus",
      ruleId: "读取校正",
      title: correctionFocus.label,
      severity: "info",
      category: "读取校正",
      page: correctionFocus.page,
      bbox: correctionFocus.bbox ?? undefined,
      description: "",
      suggestion: "",
      snippet: "",
      evidenceImage: "",
      status: "pending",
      source: "manual_correction",
    };
  }, [activeTab, correctionFocus, highlightedProblem]);

  const handleCorrectionFocus = useCallback((focus: CorrectionFocus | null) => {
    setCorrectionFocus(focus);
    if (focus) {
      setCurrentPage(focus.page);
      setIssuesExpanded(false);
    }
  }, []);

  // keepLayout：列表自动选中首条时只同步原文页码，不收起用户放大的问题区。
  const handleSelectProblem = useCallback((problemId: string | null, options?: { keepLayout?: boolean }) => {
    setSelectedProblemId(problemId);
    setSelectedEvidenceRefKey(null);
    const target = selectableProblems.find((problem) => problem.id === problemId);
    if (target) {
      const targetPage = resolveProblemTargetPage(target);
      if (targetPage !== null) {
        setCurrentPage(targetPage);
        if (!options?.keepLayout) setIssuesExpanded(false);
      }
    }
  }, [selectableProblems]);

  const handleSelectEvidenceRef = useCallback((problem: Problem, refKey: string) => {
    const ref = problem.evidenceRefs?.find((item) => item.key === refKey);
    setSelectedProblemId(problem.id);
    setSelectedEvidenceRefKey(refKey);
    if (ref) {
      setCurrentPage(ref.page);
      setIssuesExpanded(false);
    }
  }, []);

  const mutateWorkflow = useCallback(
    async (problem: Problem, status: string, note?: string): Promise<boolean> => {
      if (workflowMutationRef.current || reviewBusy || isReanalyzing || reviewContext.kind !== "available" || reviewContext.review.current_analysis.job_uuid !== jobId || !canDecideObligations(reviewContext.review)) return false;
      workflowMutationRef.current = true;
      setSubmittingIssueId(problem.id);
      setReviewNotice(null);
      try {
        const response = await fetch("/api/workflow", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "update_issue",
            job_id: jobId,
            issue_id: problem.id,
            status,
            note: typeof note === "string" ? note : workflowNotes[problem.id] ?? null,
          }),
        });
        if (activeJobIdRef.current !== jobId) return false;
        if (response.ok) {
          loadSeqRef.current += 1; reviewLoadSeqRef.current += 1; workflowLoadSeqRef.current += 1;
          invalidateReviewReads(jobId);
          setWorkflowIssues((prev) => ({ ...prev, [problem.id]: { issue_id: problem.id, status } }));
          if (typeof note === "string") {
            setWorkflowNotes((prev) => ({ ...prev, [problem.id]: note }));
          }
          setLastSavedAt(new Date());
          // 问题状态已持久化后，必须重取服务端完成条件；只更新底部计数
          // 会留下旧的 pending_findings，让最后一项处理完仍无法完成复核。
          setReviewBlockers(null);
          await Promise.all([loadReviewContext(), loadWorkflow()]);
          return activeJobIdRef.current === jobId;
        } else {
          let body: unknown = null;
          try {
            body = await response.json();
          } catch {
            body = null;
          }
          const parsed = parseReviewErrorDetail(body);
          setReviewNotice(
            parsed?.message ?? (
              response.status === 403
                ? "没有权限处理这条问题。"
                : response.status === 404
                  ? "这条问题已不属于当前分析结果，请刷新后核对。"
                  : `问题处理未能确认保存（HTTP ${response.status}），请刷新后核对。`
            ),
          );
          // 被拒时可能已完成复核或分析已更新，同步实际上下文，避免继续
          // 显示过期的可操作状态；失败的决定不写入本地“已确认/已忽略”。
          await loadReviewContext();
          return false;
        }
      } catch {
        if (activeJobIdRef.current === jobId) setReviewNotice("问题处理服务暂时不可用，请刷新后核对是否已保存。");
        return false;
      } finally {
        workflowMutationRef.current = false;
        // 写入期间切换材料时也要释放操作锁；旧请求不会改写新材料的问题状态。
        setSubmittingIssueId(null);
      }
    },
    [jobId, loadReviewContext, loadWorkflow, workflowNotes, reviewContext, reviewBusy, isReanalyzing],
  );

  const handleIssueAction = useCallback(
    async (problem: Problem, action: IssueWorkflowAction): Promise<boolean> => {
      if (action !== "note") {
        const status = action === "confirm" ? "confirmed" : action === "ignore" ? "no_issue" : "pending";
        const previousStatus = workflowIssues[problem.id]?.status ?? "pending";
        const saved = await mutateWorkflow(problem, status);
        if (saved) setLastIssueDecision({ problem, previousStatus, status });
        return saved;
      }
      // action === "note"：打开备注输入框，保存时沿用当前状态（若尚无记录则为 pending），
      // 只补充/更新 note 字段，不强行把问题状态改成某个终态。
      setNoteDialogProblem(problem);
      return false;
    },
    [mutateWorkflow, workflowIssues],
  );

  const handleUndoIssueDecision = useCallback(async (): Promise<boolean> => {
    if (!lastIssueDecision) return false;
    if (workflowIssues[lastIssueDecision.problem.id]?.status !== lastIssueDecision.status) {
      setReviewNotice("该问题的处理状态已变化，请在全部问题中核对后再修改。");
      return false;
    }
    const saved = await mutateWorkflow(lastIssueDecision.problem, lastIssueDecision.previousStatus);
    if (saved) setLastIssueDecision(null);
    return saved;
  }, [lastIssueDecision, mutateWorkflow, workflowIssues]);

  const handleSaveNote = useCallback(
    (note: string) => {
      if (!noteDialogProblem) {
        return;
      }
      const currentStatus = workflowIssues[noteDialogProblem.id]?.status ?? "pending";
      void mutateWorkflow(noteDialogProblem, currentStatus, note);
      setNoteDialogProblem(null);
    },
    [mutateWorkflow, noteDialogProblem, workflowIssues],
  );

  const handleReanalyze = useCallback(async () => {
    if (!jobId || isReanalyzing) {
      return;
    }
    setIsReanalyzing(true);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/reanalyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "dual", use_local_rules: true }),
      });
      if (response.ok) {
        setLastIssueDecision(null);
        invalidateReviewReads(jobId);
        await Promise.all([loadJobDetail(), loadWorkflow()]);
      }
    } finally {
      setIsReanalyzing(false);
    }
  }, [isReanalyzing, jobId, loadJobDetail, loadWorkflow]);

  const handleExport = useCallback(() => {
    if (!jobId || !detail) {
      return;
    }
    window.open(reportDownloadUrl(jobId, "pdf", displayedAnalysisRevision(detail)), "_blank");
  }, [jobId, detail]);

  /**
   * 完成复核：调服务端门禁，只有服务端说通过才算完成。
   *
   * 成功后刷新复核上下文与任务详情：页面必须显示"已完成复核 + 复核人 + 时间"，
   * 而不是跳走——用户需要看到这件事**真的被记下来了**（这正是此前那个假完成
   * 缺的东西）。
   */
  const handleCompleteReview = useCallback(async () => {
    if (reviewContext.kind !== "available") {
      return;
    }
    const result = await submitReviewAction(reviewContext.review.slot_id, "complete");
    if (result.ok) {
      setLastIssueDecision(null);
      await Promise.all([loadJobDetail({ silent: true }), loadWorkflow()]);
    }
  }, [loadJobDetail, loadWorkflow, reviewContext, submitReviewAction]);

  const handleStartReview = useCallback(async () => {
    if (reviewContext.kind !== "available" || reviewContext.review.current_analysis.job_uuid !== jobId) return;
    const result = await submitReviewAction(reviewContext.review.slot_id, "start");
    if (result.ok) await Promise.all([loadReviewContext(), loadWorkflow()]);
  }, [jobId, loadReviewContext, loadWorkflow, reviewContext, submitReviewAction]);

  /** 重新复核：显式 reopen（旧完成记录转失效，另起一条新会话）。 */
  const handleReopenReview = useCallback(async () => {
    if (reviewContext.kind !== "available") {
      return;
    }
    const result = await submitReviewAction(reviewContext.review.slot_id, "reopen");
    if (result.ok) {
      await Promise.all([loadJobDetail({ silent: true }), loadWorkflow()]);
    }
  }, [loadJobDetail, loadWorkflow, reviewContext, submitReviewAction]);

  /**
   * 人工补核一条检查义务（WP3-B）。
   *
   * 与复核写动作同一套错误纪律：409 的业务体（并发冲突 / 会话已换 /
   * 复核已完成）逐条展示，网络失败只说"服务暂时不可用"。成功后整段重取
   * 复核上下文——门禁、待办数、别人刚写入的结论都在这一次刷新里收敛。
   *
   * 乐观锁：改写已有结论时必须带上页面上读到的 ``decision_revision``；
   * 带旧版本被拒绝（409 obligation_decision_conflict）就让用户看到
   * "有人先改了"，而不是把别人的结论悄悄冲掉。
   */
  const handleObligationDecision = useCallback(
    async (item: ObligationReviewItemRecord, decision: string, note: string, evidenceReference: string) => {
      if (reviewContext.kind !== "available") {
        return;
      }
      const slotId = reviewContext.review.slot_id;
      setObligationSubmittingId(item.obligation_id);
      setReviewNotice(null);
      try {
        const response = await fetch(
          `/api/reviews/${encodeURIComponent(slotId)}/obligations/${encodeURIComponent(item.obligation_id)}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              decision,
              note: note.trim() || undefined,
              evidence_reference: evidenceReference.trim() || undefined,
              job_uuid: jobId,
              ...(item.decision_revision !== null
                ? { expected_revision: item.decision_revision }
                : {}),
            }),
          },
        );
        if (response.ok) {
          reviewLoadSeqRef.current += 1;
          workflowLoadSeqRef.current += 1;
          invalidateReviewReads(jobId);
          setReviewBlockers(null);
          await Promise.all([loadReviewContext(), loadWorkflow()]);
          return;
        }
        let body: unknown = null;
        try {
          body = await response.json();
        } catch {
          body = null;
        }
        const parsed = parseReviewErrorDetail(body);
        if (parsed) {
          if (parsed.blockers && parsed.blockers.length > 0) {
            setReviewBlockers(parsed);
          } else {
            // 无 blockers 的 409（并发冲突 / 复核已完成锁定 / 尚未开始复核）：
            // blockers 区不渲染空列表，业务消息必须落到通知条——
            // 否则"点了按钮没反应"，与"409 只显示状态码"同害。
            setReviewNotice(parsed.message);
          }
          // 被拒意味着页面事实可能已过期：静默重取，把面板上的
          // revision / 结论 / 门禁刷成服务端真值。
          await loadReviewContext();
          return;
        }
        setReviewNotice(
          response.status === 403
            ? "没有权限对这条材料执行该操作。"
            : response.status === 404
              ? "这条检查义务不在当前复核范围内（可能分析已更新）。"
              : `操作未完成（HTTP ${response.status}）。`,
        );
      } catch {
        setReviewNotice("复核服务暂时不可用，请稍后重试。");
      } finally {
        setObligationSubmittingId(null);
      }
    },
    [jobId, loadReviewContext, loadWorkflow, reviewContext],
  );

  if (!jobId) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 text-center" data-testid="gbc-review-no-job">
        <p className="text-sm text-slate-500">请从处理队列或任务历史选择一份材料进入审核工作台。</p>
        <Link href={"/queue" as Route} className="mt-3 text-sm font-medium text-primary-600 hover:text-primary-700">
          前往处理队列
        </Link>
      </div>
    );
  }

  if (loading) {
    return (
      <ReviewStateFrame>
        <div className="flex h-full items-center justify-center" data-testid="gbc-review-loading">
          <span className="text-sm text-slate-500">正在加载任务详情…</span>
        </div>
      </ReviewStateFrame>
    );
  }

  if (!detail) {
    return (
      <ReviewStateFrame>
        <div className="flex h-full flex-col items-center justify-center gap-3" data-testid="gbc-review-not-found">
          <span className="text-sm text-slate-500">未找到对应任务，或任务详情暂时无法读取。</span>
          <Button size="sm" variant="secondary" onClick={() => void loadJobDetail()}>重新加载</Button>
        </div>
      </ReviewStateFrame>
    );
  }

  // 修复 B：未分析完成的任务（queued/processing）没有可复核内容。列表入口已对
  // 这类任务禁用，这里兜底处理直接手敲 URL 进入的情况——给出明确提示与返回
  // 路径，不渲染空的三栏（那是另一种形式的"白屏"）。
  if (!isUiTaskFinished(normalizeUiTaskStatus(detail.status))) {
    return (
      <ReviewStateFrame>
      <div
        className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center"
        data-testid="gbc-review-not-ready"
      >
        <p className="text-sm font-medium text-slate-700">
          {normalizeUiTaskStatus(detail.status) === "pending_analysis"
            ? "该任务尚未开始分析，暂无审核内容。"
            : "该任务尚未分析完成，暂无审核内容。"}
        </p>
        <p className="text-xs text-slate-500">
          {normalizeUiTaskStatus(detail.status) === "pending_analysis"
            ? "请在处理队列对该任务点击「开始分析」；分析结束后可进入审核工作台。"
            : "请等待分析结束后再进入审核工作台；可在处理队列查看实时进度。"}
        </p>
        <Link href={"/queue" as Route} className="mt-2 text-sm font-medium text-primary-600 hover:text-primary-700">
          前往处理队列
        </Link>
        <AnalysisCheckProgress value={detail.check_progress} />
      </div>
      </ReviewStateFrame>
    );
  }

  const headerBadge = resolveWorkbenchHeaderBadge(detail);
  const filename = String(detail.filename ?? jobId);
  const reportId = String(detail.structured_report_id ?? "").trim();

  const reviewData = reviewContext.kind === "available" ? reviewContext.review : null;
  const currentReviewJob = reviewData?.current_analysis.job_uuid === jobId;
  const canEditReview = currentReviewJob && canDecideObligations(reviewData);
  const showStartReview = currentReviewJob && canStartReview(reviewData);
  const reviewBadge = resolveReviewBadge(reviewData);
  const completedSummary = describeCompletedReview(latestCompletedSession(reviewData));
  const invalidated = latestInvalidatedSession(reviewData);
  const completeButton = resolveCompleteButtonState(reviewData, statusCounts.pending);
  /** 人工补核块（WP3-B）：老后端/旧数据没有该字段时按"无此页签"处理。 */
  const obligationReview = reviewData?.obligation_review ?? null;
  const pendingObligations = pendingObligationCount(reviewData);
  const rightTabs: { id: RightTabId; label: string }[] = [
    { id: "issues", label: "审核问题" },
    ...(obligationReview
      ? [
          {
            id: "obligations" as const,
            label:
              pendingObligations && pendingObligations > 0
                ? `检查补核 (${pendingObligations})`
                : "检查补核",
          },
        ]
      : []),
    { id: "corrections", label: "读取校正" },
    { id: "metadata", label: "元数据" },
    { id: "stages", label: "阶段记录" },
  ];
  const blockerLines = describeBlockers(reviewBlockers?.blockers ?? []);
  /**
   * 服务端算出的门禁阻塞：**常驻**显示，而不是等用户点了按钮才出现。
   *
   * 两个理由：
   * 1. 按钮在"还有待处理问题"时是 disabled 的，用户根本点不下去——若只在点击
   *    之后才显示原因，他就只能看着一个灰按钮，不知道还差什么（§七十九）；
   * 2. 版本已换、分析已重跑这类事实过期，是**打开页面就该知道**的事，
   *    藏在一个需要点击才能触发的提示里等于没说。
   */
  const gateBlockerLines = describeBlockers(reviewData?.completion_gate?.blockers ?? []);
  const blockerLimit = showAllBlockers ? Infinity : COLLAPSED_BLOCKER_COUNT;
  const visibleBlockerLines = blockerLines.slice(0, blockerLimit);
  const visibleGateBlockerLines = gateBlockerLines.slice(0, Math.max(0, blockerLimit - visibleBlockerLines.length));
  const hiddenBlockerCount = blockerLines.length + gateBlockerLines.length - visibleBlockerLines.length - visibleGateBlockerLines.length;
  const previewNotice = reviewData && !canEditReview && !completeButton.completed
    ? !currentReviewJob ? "当前查看的是历史分析，仅供查看。请从复核清单选择当前材料。"
      : !reviewData.current_analysis.completed ? "当前版本尚未产出可复核的分析结果，请在处理队列检查进度。"
        : "当前为材料预览。点击「开始复核」后可提交问题判定和检查补核。"
    : null;
  const showInvalidatedNotice = Boolean(invalidated && !completedSummary && !canEditReview);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden" data-testid="gbc-review-workbench-page">
      <div
        ref={splitPaneRef}
        className="grid min-h-0 min-w-0 flex-1 overflow-hidden"
        style={{ gridTemplateColumns: issuesExpanded ? "minmax(0, 1fr)" : `minmax(0, ${documentShare}fr) 8px minmax(0, ${100 - documentShare}fr)` }}
        data-testid="gbc-review-split-pane"
      >
        <div className={`min-h-0 min-w-0 overflow-hidden bg-white ${issuesExpanded ? "hidden" : "flex"}`} data-testid="gbc-review-document-pane">
          {showThumbnails ? (
            <div className="w-40 shrink-0 overflow-hidden border-r border-border">
              <ThumbnailRail jobId={jobId} totalPages={totalPages} currentPage={currentPage} onSelectPage={setCurrentPage} />
            </div>
          ) : null}
          <div className="min-h-0 min-w-0 flex-1">
          <PdfViewerPane
            jobId={jobId}
            totalPages={totalPages}
            currentPage={currentPage}
            onPageChange={(page) => setCurrentPage(Math.max(1, page))}
            highlightedProblem={pdfHighlight}
            leadingControls={
              <button
                type="button"
                onClick={() => setShowThumbnails((value) => !value)}
                aria-expanded={showThumbnails}
                aria-label={showThumbnails ? "收起缩略图" : "展开缩略图"}
                data-testid="gbc-review-toggle-thumbnails"
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-slate-600 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
              >
                <PanelsTopLeft className="h-4 w-4" aria-hidden="true" />缩略图
              </button>
            }
          />
          </div>
        </div>
        {!issuesExpanded ? (
          <div
            role="separator"
            tabIndex={0}
            aria-label="调整原文与问题区宽度"
            aria-orientation="vertical"
            aria-valuemin={35}
            aria-valuemax={65}
            aria-valuenow={documentShare}
            data-testid="gbc-review-resize-handle"
            className="flex touch-none cursor-col-resize items-center justify-center border-x border-border bg-surface-100 hover:bg-primary-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-400"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              event.preventDefault();
            }}
            onPointerMove={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              const bounds = splitPaneRef.current?.getBoundingClientRect();
              if (!bounds || bounds.width <= 0) return;
              setDocumentShare(clampDocumentShare(((event.clientX - bounds.left) / bounds.width) * 100));
            }}
            onPointerUp={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              event.currentTarget.releasePointerCapture(event.pointerId);
              persistDocumentShare(documentShare);
            }}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home") return;
              event.preventDefault();
              const next = event.key === "Home" ? DEFAULT_DOCUMENT_SHARE : clampDocumentShare(documentShare + (event.key === "ArrowLeft" ? -2 : 2));
              setDocumentShare(next);
              persistDocumentShare(next);
            }}
          ><span className="h-8 w-0.5 rounded bg-slate-300" /></div>
        ) : null}
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-white" data-testid="gbc-review-problems-pane">
          <EmbeddedWorkspaceTopbar
            leading={
              <>
                <span className="min-w-0 truncate text-sm font-semibold text-slate-900" title={filename} data-testid="gbc-review-filename">
                  {filename}
                </span>
                <span className="shrink-0"><Badge tone={headerBadge.tone}>{headerBadge.label}</Badge></span>
                {reviewBadge ? (
                  <span className="shrink-0" data-testid="gbc-review-lifecycle-badge">
                    <Badge tone={reviewBadge.tone}>{reviewBadge.label}</Badge>
                  </span>
                ) : null}
              </>
            }
          />
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-white px-3 py-1.5">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={() => router.push(returnTo as Route)}
                aria-label={returnTo.startsWith("/review") ? "返回复核清单" : "返回来源列表"}
                data-testid="gbc-review-back-button"
                className="shrink-0 rounded-md px-1.5 py-1 text-sm text-slate-400 transition-colors hover:bg-surface-100 hover:text-slate-700"
              >
                ← {returnTo.startsWith("/review") ? "清单" : "返回"}
              </button>
              <span className="min-w-0 text-xs text-slate-400" data-testid="gbc-review-header-meta">
                {reportId ? `报告 ${reportId} · ` : ""}
                {totalPages !== null ? `${totalPages} 页` : "页数未知"}
                {" · "}<Link href={"/reports" as Route} className="text-primary-700 hover:underline" data-testid="gbc-review-department-reports">按部门 / 年度查看报告</Link>
                {completedSummary ? (
                  <span data-testid="gbc-review-completed-summary"> · 已完成复核：{completedSummary}</span>
                ) : null}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <ReviewDetailNavigation jobId={jobId} returnTo={returnTo} />
              <Button size="sm" variant="secondary" onClick={() => void handleReanalyze()} disabled={isReanalyzing || submittingIssueId !== null} data-testid="gbc-review-reanalyze">
                {isReanalyzing ? "重新分析中…" : "重新分析"}
              </Button>
              <Button size="sm" variant="secondary" onClick={handleExport} data-testid="gbc-review-export">
                导出报告
              </Button>
              {showStartReview ? <Button size="sm" variant="primary" onClick={() => void handleStartReview()} disabled={reviewBusy} data-testid="gbc-review-start">{reviewBusy ? "提交中…" : invalidated ? "重新开始复核" : "开始复核"}</Button> : null}
              <Button
                size="sm"
                variant="primary"
                onClick={() =>
                  void (completeButton.completed ? handleReopenReview() : handleCompleteReview())
                }
                disabled={
                  reviewBusy || submittingIssueId !== null ||
                  !currentReviewJob ||
                  reviewContext.kind !== "available" ||
                  (!completeButton.completed && !completeButton.enabled)
                }
                data-testid="gbc-review-complete"
                title={
                  reviewContext.kind !== "available"
                    ? REVIEW_CONTEXT_UNAVAILABLE_MESSAGE
                    : completeButton.hint || undefined
                }
              >
                {reviewBusy ? "提交中…" : completeButton.label}
              </Button>
            </div>
          </div>
          {/* 复核生命周期的状态与拒绝原因：都必须出现在页面上，而不是只留在控制台。
              「为什么不能完成复核」是这个页面最有价值的信息，只显示 HTTP 409 等于
              把它丢掉。预览说明、失效说明和阻塞原因合并成一条状态条，不再逐条
              叠成多层横幅挤占问题区；原因较多时先收起，可展开查看全部。*/}
          {reviewNotice ? (
            <div
              className="shrink-0 border-b border-danger-200 bg-danger-50 px-3 py-1.5 text-xs text-danger-700"
              data-testid="gbc-review-action-notice"
            >
              {reviewNotice}
            </div>
          ) : null}
          {previewNotice || reviewContext.kind === "unavailable" || showInvalidatedNotice || blockerLines.length > 0 || gateBlockerLines.length > 0 ? (
            <div
              className={`flex shrink-0 flex-wrap items-baseline gap-x-4 gap-y-0.5 border-b px-3 py-1.5 text-xs ${
                blockerLines.length > 0 || gateBlockerLines.length > 0 ? "border-warning-200 bg-warning-50" : "border-border bg-surface-50"
              }`}
              data-testid="gbc-review-status-strip"
            >
              {previewNotice ? <span className="text-slate-600" data-testid="gbc-review-preview-notice">{previewNotice}</span> : null}
              {reviewContext.kind === "unavailable" ? (
                <span className="text-warning-700" data-testid="gbc-review-context-unavailable">{reviewContext.message}</span>
              ) : null}
              {showInvalidatedNotice && invalidated ? (
                <span className="text-warning-700" data-testid="gbc-review-invalidated-notice">
                  {invalidationReasonLabel(invalidated.invalidated_reason) || "复核已失效，需要重新复核"}
                </span>
              ) : null}
              {visibleBlockerLines.length > 0 ? (
                <span className="text-danger-700" data-testid="gbc-review-complete-blockers">
                  <span className="font-medium">当前无法完成复核：</span>
                  {visibleBlockerLines.map((line, index) => (
                    <Fragment key={line}>
                      {index > 0 ? "；" : null}
                      <span data-testid="gbc-review-complete-blocker-item">{line}</span>
                    </Fragment>
                  ))}
                </span>
              ) : null}
              {visibleGateBlockerLines.length > 0 ? (
                <span className="text-warning-700" data-testid="gbc-review-gate-blockers">
                  <span className="font-medium">当前无法完成复核：</span>
                  {visibleGateBlockerLines.map((line, index) => (
                    <Fragment key={line}>
                      {index > 0 ? "；" : null}
                      <span data-testid="gbc-review-gate-blocker-item">{line}</span>
                    </Fragment>
                  ))}
                </span>
              ) : null}
              {blockerLines.length + gateBlockerLines.length > COLLAPSED_BLOCKER_COUNT ? (
                <button
                  type="button"
                  onClick={() => setShowAllBlockers((value) => !value)}
                  aria-expanded={showAllBlockers}
                  data-testid="gbc-review-toggle-blockers"
                  className="text-primary-700 hover:underline"
                >
                  {showAllBlockers ? "收起" : `另有 ${hiddenBlockerCount} 项，展开`}
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="flex shrink-0 flex-wrap items-center border-b border-border text-sm">
            {rightTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                data-testid={`gbc-review-tab-${tab.id}`}
                aria-pressed={activeTab === tab.id}
                className={`border-b-2 px-3 py-2 text-xs font-medium transition-colors ${
                  activeTab === tab.id
                    ? "border-primary-600 text-primary-700"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                }`}
              >
                {tab.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setIssuesExpanded((value) => !value)}
              aria-pressed={issuesExpanded}
              aria-label={issuesExpanded ? "还原左右对照" : "放大问题区"}
              data-testid="gbc-review-expand-issues"
              className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs text-slate-500 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              {issuesExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              {issuesExpanded ? "还原" : "放大"}
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            {activeTab === "issues" ? (
              <IssuesTab
                key={jobId}
                problems={problems}
                sourceFindings={sourceFindings}
                selectedProblemId={selectedProblemId}
                onSelectProblem={handleSelectProblem}
                workflowStatusByIssueId={workflowStatusByIssueId}
                onAction={handleIssueAction}
                submittingIssueId={submittingIssueId}
                lastDecision={lastIssueDecision}
                onUndo={handleUndoIssueDecision}
                pendingObligations={pendingObligations ?? 0}
                onOpenObligations={() => setActiveTab("obligations")}
                onSelectEvidenceRef={handleSelectEvidenceRef}
                readOnly={!canEditReview}
                wide={issuesExpanded}
              />
            ) : activeTab === "obligations" && obligationReview ? (
              <ObligationsTab
                block={obligationReview}
                submittingId={obligationSubmittingId}
                readOnly={!canEditReview}
                onDecide={(item, decision, note, evidenceReference) =>
                  void handleObligationDecision(item, decision, note, evidenceReference)
                }
              />
            ) : activeTab === "corrections" ? (
              <CorrectionsTab
                key={`${jobId}:${displayedAnalysisRevision(detail) ?? ""}`}
                jobId={jobId}
                disabled={isReanalyzing}
                onFocus={handleCorrectionFocus}
                onSubmitted={() => {
                  setLastIssueDecision(null);
                  invalidateReviewReads(jobId);
                  // 静默刷新：留住页签上的提交结果；重分析开始后页面会自动切到进度视图。
                  void Promise.all([loadJobDetail({ silent: true }), loadWorkflow()]);
                }}
              />
            ) : activeTab === "metadata" ? (
              <MetadataTab job={detail} detail={detail} />
            ) : (
              <><AnalysisCheckProgress value={detail.check_progress} /><StageHistoryTab
                stageProgress={detail.stage_progress as { phase?: string | null; percent?: number | null } | null}
                stageFailedAt={detail.stage_failed_at as { phase?: string | null } | null}
              /></>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-1 border-t border-border bg-white px-3 py-1.5 text-xs text-slate-500">
            <div data-testid="gbc-review-status-bar-counts">
              正式问题处理：已确认 {statusCounts.confirmed} · 已忽略 {statusCounts.ignored} · 已进整改包{" "}
              {statusCounts.inPackage} · 待复核 {statusCounts.needsReview} · 待处理{" "}
              {statusCounts.pending}
            </div>
            <div data-testid="gbc-review-status-bar-saved-at">
              {lastSavedAt ? `自动保存于 ${formatSavedAtTime(lastSavedAt)}` : "尚无保存记录"}
            </div>
          </div>
        </div>
      </div>

      {noteDialogProblem ? (
        <IssueNoteDialog
          problemTitle={noteDialogProblem.title}
          initialNote={workflowNotes[noteDialogProblem.id] ?? ""}
          onSave={handleSaveNote}
          onCancel={() => setNoteDialogProblem(null)}
          isSubmitting={submittingIssueId === noteDialogProblem.id}
        />
      ) : null}
    </div>
  );
}

export default ReviewWorkbenchPage;
