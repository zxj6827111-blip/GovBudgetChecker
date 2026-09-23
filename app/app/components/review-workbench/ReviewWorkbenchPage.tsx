/**
 * ReviewWorkbenchPage：Task 6，1:1 还原 `03-review-workbench.png`——把现有
 * "点问题弹模态框看单页"改成常驻三栏工作区。
 *
 * 入口约定：`/review?job={job_id}`（与既有 `/queue?job={job_id}` 同构的查询参数
 * 命名），未带 job 参数时显示"请从处理队列或任务历史选择一份材料"的引导态，
 * 不假装有默认任务。
 *
 * 三栏：左 页面缩略图栏（ThumbnailRail） | 中 PDF 视图（PdfViewerPane） |
 * 右 问题/元数据/阶段记录 tab（IssuesTab/MetadataTab/StageHistoryTab）。
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
 * 3. 进入工作台可以幂等自动开复核，但**绝不自动完成**：没有待办不等于人已经确认。
 */
"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button } from "@/components/ui";
import { resolvePollingDecision } from "@/lib/jobPolling";
import type { Problem } from "@/lib/mock";
import { describeBlockers, invalidationReasonLabel } from "@/lib/reviewLifecyclePresentation";
import type { JobDetailRecord, JobSummaryRecord, StructuredIngestRecord } from "@/lib/uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus, toUiProblems } from "@/lib/uiAdapters";

import { IssueNoteDialog } from "./IssueNoteDialog";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssuesTab } from "./IssuesTab";
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
  shouldAutoStartReview,
  toReviewContextState,
  type ObligationReviewItemRecord,
  type ReviewByJobDataRecord,
  type ReviewContextState,
  type ReviewErrorDetail,
} from "./reviewLifecycleAdapters";
import {
  computeWorkflowStatusCounts,
  extractTotalPageCount,
  pickAutoSelectJob,
  resolveProblemTargetPage,
  resolveWorkbenchHeaderBadge,
  type WorkflowIssueRecord,
} from "./reviewWorkbenchAdapters";
import { StageHistoryTab } from "./StageHistoryTab";
import { ThumbnailRail } from "./ThumbnailRail";
import { useJobPolling } from "../workspace/useJobPolling";

type RightTabId = "issues" | "metadata" | "stages" | "obligations";

interface WorkflowStateResponse {
  issues?: Record<string, { issue_id: string; job_id: string; status: string; note?: string | null }>;
}

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return fallback;
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

function formatSavedAtTime(date: Date): string {
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

export function ReviewWorkbenchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const jobId = searchParams.get("job") ?? "";

  const [detail, setDetail] = useState<JobDetailRecord | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [activeTab, setActiveTab] = useState<RightTabId>("issues");
  const [selectedProblemId, setSelectedProblemId] = useState<string | null>(null);
  const [workflowIssues, setWorkflowIssues] = useState<Record<string, WorkflowIssueRecord>>({});
  const [workflowNotes, setWorkflowNotes] = useState<Record<string, string>>({});
  const [submittingIssueId, setSubmittingIssueId] = useState<string | null>(null);
  const [noteDialogProblem, setNoteDialogProblem] = useState<Problem | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [isReanalyzing, setIsReanalyzing] = useState(false);
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
  /** 无 job 参数时已尝试过自动选择（只试一次，避免每次渲染都重复请求）。 */
  const [autoSelectDone, setAutoSelectDone] = useState(false);
  const loadSeqRef = useRef(0);
  /** 自动开复核只尝试一次：重复调用虽然幂等，但会让网络面板刷满请求。 */
  const autoStartTriedRef = useRef(false);

  /** 最新 detail 的旁路引用：轮询决策每次续排时实时读取。 */
  const detailRef = useRef<JobDetailRecord | null>(null);
  detailRef.current = detail;

  /**
   * 无 job 参数时的自动选择：侧边栏「审核工作台」href 是 /review（不带参数），
   * 直接点击会停在引导态。进入时自动挑最近一个待人工复核任务跳转；
   * 没有可复核任务时保持引导态（不假装有默认任务）。
   */
  useEffect(() => {
    if (jobId || autoSelectDone) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/api/jobs", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as
          | JobSummaryRecord[]
          | { items?: JobSummaryRecord[] };
        const jobs = Array.isArray(payload) ? payload : payload.items ?? [];
        const next = pickAutoSelectJob(jobs);
        if (!cancelled && next?.job_id) {
          router.replace(`/review?job=${encodeURIComponent(next.job_id)}`);
        }
      } catch {
        // 拉取失败时保持引导态，不阻断页面。
      } finally {
        if (!cancelled) {
          setAutoSelectDone(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, autoSelectDone, router]);

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
    let payload: ReviewByJobDataRecord | null = null;
    try {
      const response = await fetch(`/api/reviews?job_uuid=${encodeURIComponent(jobId)}`, {
        cache: "no-store",
      });
      if (response.ok) {
        const body = (await response.json()) as { data?: ReviewByJobDataRecord };
        payload = body.data ?? null;
      }
    } catch {
      payload = null;
    }
    const next = toReviewContextState(payload);
    setReviewContext(next);
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
          setReviewBlockers(null);
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
      const nextProblems = toUiProblems({ ...jobDetail, structured_ingest: structured }).map((problem) => ({
        ...problem,
        jobId: jobDetail.job_id,
      }));
      // 同步更新旁路引用（理由同各列表页 fetcher 内注释：轮询决策先于重渲染执行）。
      detailRef.current = jobDetail;
      setDetail(jobDetail);
      setProblems(nextProblems);
      setLoading(false);

      // 任务可复核时才解析复核上下文：未分析完成的材料没有可复核的对象，
      // 此时请求只会换来一个必然失败的 409。自动开复核只尝试一次
      // （虽然幂等，但重复调用会让网络面板刷满请求）。
      if (isUiTaskFinished(normalizeUiTaskStatus(jobDetail.status)) && !autoStartTriedRef.current) {
        autoStartTriedRef.current = true;
        const context = await loadReviewContext();
        if (context.kind === "available" && shouldAutoStartReview(context.review)) {
          const started = await submitReviewAction(context.review.slot_id, "start");
          if (started.ok) {
            await loadReviewContext();
          }
        }
      }
    },
    [jobId, loadReviewContext, submitReviewAction],
  );

  /**
   * 装载问题工作流状态（`/api/workflow` 是问题人工处理状态的**唯一**路径）。
   *
   * 只保留当前任务的记录：同一个 store 里混着全部任务的问题决定，
   * 不过滤会让另一份材料的"已确认"显示在本页上。
   */
  const loadWorkflow = useCallback(async () => {
    const payload = await fetchJson<WorkflowStateResponse>("/api/workflow", {});
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

  // 首次装载：任务详情 + 问题工作流状态。
  // 两者都要：只拉详情会让"已确认/已忽略"在刷新后归零（工作流状态是另一条接口），
  // 用户会以为自己的处理动作丢了。
  useEffect(() => {
    void loadJobDetail();
    void loadWorkflow();
  }, [loadJobDetail, loadWorkflow]);

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
      return { kind: "stop" as const };
    }, []),
  });

  useEffect(() => {
    setCurrentPage(1);
    setSelectedProblemId(null);
    // 切换任务时重置复核状态：否则上一条材料的 blockers 会挂在新材料的页面上。
    autoStartTriedRef.current = false;
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

  const selectedProblem = useMemo(
    () => problems.find((problem) => problem.id === selectedProblemId) ?? null,
    [problems, selectedProblemId],
  );

  const handleSelectProblem = useCallback((problemId: string) => {
    setSelectedProblemId(problemId);
    const target = problems.find((problem) => problem.id === problemId);
    if (target) {
      const targetPage = resolveProblemTargetPage(target);
      if (targetPage !== null) {
        setCurrentPage(targetPage);
      }
    }
  }, [problems]);

  const mutateWorkflow = useCallback(
    async (problem: Problem, status: string, note?: string) => {
      setSubmittingIssueId(problem.id);
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
        if (response.ok) {
          setWorkflowIssues((prev) => ({ ...prev, [problem.id]: { issue_id: problem.id, status } }));
          if (typeof note === "string") {
            setWorkflowNotes((prev) => ({ ...prev, [problem.id]: note }));
          }
          setLastSavedAt(new Date());
        }
      } finally {
        setSubmittingIssueId(null);
      }
    },
    [jobId, workflowNotes],
  );

  const handleIssueAction = useCallback(
    (problem: Problem, action: IssueWorkflowAction) => {
      if (action === "confirm") {
        void mutateWorkflow(problem, "confirmed");
        return;
      }
      if (action === "ignore") {
        void mutateWorkflow(problem, "no_issue");
        return;
      }
      // action === "note"：打开备注输入框，保存时沿用当前状态（若尚无记录则为 pending），
      // 只补充/更新 note 字段，不强行把问题状态改成某个终态。
      setNoteDialogProblem(problem);
    },
    [mutateWorkflow],
  );

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
        body: JSON.stringify({ mode: "dual", use_local_rules: true, use_ai_assist: true }),
      });
      if (response.ok) {
        await loadJobDetail();
      }
    } finally {
      setIsReanalyzing(false);
    }
  }, [isReanalyzing, jobId, loadJobDetail]);

  const handleExport = useCallback(() => {
    if (!jobId) {
      return;
    }
    window.open(`/api/reports/download?job_id=${encodeURIComponent(jobId)}&format=pdf`, "_blank");
  }, [jobId]);

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
      await loadReviewContext();
      await loadJobDetail({ silent: true });
    }
  }, [loadJobDetail, loadReviewContext, reviewContext, submitReviewAction]);

  /** 重新复核：显式 reopen（旧完成记录转失效，另起一条新会话）。 */
  const handleReopenReview = useCallback(async () => {
    if (reviewContext.kind !== "available") {
      return;
    }
    const result = await submitReviewAction(reviewContext.review.slot_id, "reopen");
    if (result.ok) {
      await loadReviewContext();
      await loadJobDetail({ silent: true });
    }
  }, [loadJobDetail, loadReviewContext, reviewContext, submitReviewAction]);

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
    async (item: ObligationReviewItemRecord, decision: string, note: string) => {
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
              job_uuid: jobId,
              ...(item.decision_revision !== null
                ? { expected_revision: item.decision_revision }
                : {}),
            }),
          },
        );
        if (response.ok) {
          setReviewBlockers(null);
          await loadReviewContext();
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
    [jobId, loadReviewContext, reviewContext],
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
      <div className="flex h-full items-center justify-center" data-testid="gbc-review-loading">
        <span className="text-sm text-slate-500">正在加载任务详情…</span>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="gbc-review-not-found">
        <span className="text-sm text-slate-500">未找到对应任务。</span>
      </div>
    );
  }

  // 修复 B：未分析完成的任务（queued/processing）没有可复核内容。列表入口已对
  // 这类任务禁用，这里兜底处理直接手敲 URL 进入的情况——给出明确提示与返回
  // 路径，不渲染空的三栏（那是另一种形式的"白屏"）。
  if (!isUiTaskFinished(normalizeUiTaskStatus(detail.status))) {
    return (
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
      </div>
    );
  }

  const headerBadge = resolveWorkbenchHeaderBadge(detail);
  const filename = String(detail.filename ?? jobId);
  const reportId = String(detail.structured_report_id ?? "").trim();

  const reviewData = reviewContext.kind === "available" ? reviewContext.review : null;
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

  return (
    <div className="flex h-full flex-col overflow-hidden" data-testid="gbc-review-workbench-page">
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-white px-6 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => router.push("/queue" as Route)}
            aria-label="返回"
            data-testid="gbc-review-back-button"
            className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-surface-100 hover:text-slate-700"
          >
            ←
          </button>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-slate-900" data-testid="gbc-review-filename">
                {filename}
              </span>
              <Badge tone={headerBadge.tone}>{headerBadge.label}</Badge>
              {reviewBadge ? (
                <span data-testid="gbc-review-lifecycle-badge">
                  <Badge tone={reviewBadge.tone}>{reviewBadge.label}</Badge>
                </span>
              ) : null}
            </div>
            <div className="mt-0.5 text-xs text-slate-400" data-testid="gbc-review-header-meta">
              {reportId ? `报告 ${reportId} · ` : ""}
              {totalPages !== null ? `${totalPages} 页` : "页数未知"}
              {completedSummary ? (
                <span data-testid="gbc-review-completed-summary"> · 已完成复核：{completedSummary}</span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => void handleReanalyze()} disabled={isReanalyzing} data-testid="gbc-review-reanalyze">
            {isReanalyzing ? "重新分析中…" : "重新分析"}
          </Button>
          <Button variant="secondary" onClick={handleExport} data-testid="gbc-review-export">
            导出报告
          </Button>
          <Button
            variant="primary"
            onClick={() =>
              void (completeButton.completed ? handleReopenReview() : handleCompleteReview())
            }
            disabled={
              reviewBusy ||
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
          把它丢掉。*/}
      {reviewContext.kind === "unavailable" ? (
        <div
          className="shrink-0 border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-800"
          data-testid="gbc-review-context-unavailable"
        >
          {reviewContext.message}
        </div>
      ) : null}
      {invalidated && !completedSummary ? (
        <div
          className="shrink-0 border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-800"
          data-testid="gbc-review-invalidated-notice"
        >
          {invalidationReasonLabel(invalidated.invalidated_reason) ||
            "复核已失效，需要重新复核"}{" "}
        </div>
      ) : null}
      {reviewNotice ? (
        <div
          className="shrink-0 border-b border-rose-200 bg-rose-50 px-6 py-2 text-xs text-rose-700"
          data-testid="gbc-review-action-notice"
        >
          {reviewNotice}
        </div>
      ) : null}
      {blockerLines.length > 0 ? (
        <div
          className="shrink-0 border-b border-rose-200 bg-rose-50 px-6 py-2 text-xs text-rose-700"
          data-testid="gbc-review-complete-blockers"
        >
          <span className="font-medium">当前无法完成复核：</span>
          <ul className="mt-1 list-disc pl-5">
            {blockerLines.map((line) => (
              <li key={line} data-testid="gbc-review-complete-blocker-item">
                {line}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {gateBlockerLines.length > 0 ? (
        <div
          className="shrink-0 border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-800"
          data-testid="gbc-review-gate-blockers"
        >
          <span className="font-medium">当前无法完成复核：</span>
          <ul className="mt-1 list-disc pl-5">
            {gateBlockerLines.map((line) => (
              <li key={line} data-testid="gbc-review-gate-blocker-item">
                {line}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid flex-1 grid-cols-[220px_1fr_360px] overflow-hidden">
        <div className="overflow-hidden border-r border-border bg-white">
          <ThumbnailRail jobId={jobId} totalPages={totalPages} currentPage={currentPage} onSelectPage={setCurrentPage} />
        </div>

        <div className="overflow-hidden bg-white">
          <PdfViewerPane
            jobId={jobId}
            totalPages={totalPages}
            currentPage={currentPage}
            onPageChange={(page) => setCurrentPage(Math.max(1, page))}
            highlightedProblem={selectedProblem}
          />
        </div>

        <div className="flex flex-col overflow-hidden border-l border-border bg-white">
          <div className="flex shrink-0 border-b border-border text-sm">
            {rightTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                data-testid={`gbc-review-tab-${tab.id}`}
                className={`flex-1 border-b-2 px-3 py-2.5 font-medium transition-colors ${
                  activeTab === tab.id
                    ? "border-primary-600 text-primary-700"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-hidden">
            {activeTab === "issues" ? (
              <IssuesTab
                problems={problems}
                selectedProblemId={selectedProblemId}
                onSelectProblem={handleSelectProblem}
                workflowStatusByIssueId={workflowStatusByIssueId}
                onAction={handleIssueAction}
                submittingIssueId={submittingIssueId}
              />
            ) : activeTab === "obligations" && obligationReview ? (
              <ObligationsTab
                block={obligationReview}
                submittingId={obligationSubmittingId}
                readOnly={!canDecideObligations(reviewData)}
                onDecide={(item, decision, note) =>
                  void handleObligationDecision(item, decision, note)
                }
              />
            ) : activeTab === "metadata" ? (
              <MetadataTab job={detail} detail={detail} />
            ) : (
              <StageHistoryTab
                stageProgress={detail.stage_progress as { phase?: string | null; percent?: number | null } | null}
                stageFailedAt={detail.stage_failed_at as { phase?: string | null } | null}
              />
            )}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-border bg-white px-6 py-2 text-xs text-slate-500">
        <div data-testid="gbc-review-status-bar-counts">
          已确认 {statusCounts.confirmed} · 已忽略 {statusCounts.ignored} · 已进整改包{" "}
          {statusCounts.inPackage} · 待复核 {statusCounts.needsReview} · 待处理{" "}
          {statusCounts.pending}
        </div>
        <div data-testid="gbc-review-status-bar-saved-at">
          {lastSavedAt ? `自动保存于 ${formatSavedAtTime(lastSavedAt)}` : "尚无保存记录"}
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
