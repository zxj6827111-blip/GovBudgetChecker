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
 * - `/api/files/{job_id}/preview`：缩略图与中栏大图（已有接口，无需新增后端）。
 *
 * 「完成复核」按钮的诚实边界：后端没有"标记任务复核完成"的端点（调研阶段确认
 * 过，`api/routes/jobs.py`/`api/routes/workflow.py` 均无此类写操作），因此本按钮
 * 只在全部问题都已确认/忽略（pending=0）时才可点击，点击后返回处理队列——
 * 它是一个诚实的"确认你已经处理完当前列表"的前端把关，不向后端发送任何
 * 声称"审核已完成"的请求，避免承诺系统做不到的事。此为本批已知边界，
 * 已在交付说明中报告。
 */
"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge, Button } from "@/components/ui";
import type { Problem } from "@/lib/mock";
import type { JobDetailRecord, StructuredIngestRecord } from "@/lib/uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus, toUiProblems } from "@/lib/uiAdapters";

import { IssueNoteDialog } from "./IssueNoteDialog";
import type { IssueWorkflowAction } from "./IssueCard";
import { IssuesTab } from "./IssuesTab";
import { MetadataTab } from "./MetadataTab";
import { PdfViewerPane } from "./PdfViewerPane";
import {
  computeWorkflowStatusCounts,
  extractTotalPageCount,
  resolveProblemTargetPage,
  resolveWorkbenchHeaderBadge,
  type WorkflowIssueRecord,
} from "./reviewWorkbenchAdapters";
import { StageHistoryTab } from "./StageHistoryTab";
import { ThumbnailRail } from "./ThumbnailRail";

type RightTabId = "issues" | "metadata" | "stages";

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
  const loadSeqRef = useRef(0);

  const loadJobDetail = useCallback(async () => {
    if (!jobId) {
      setLoading(false);
      return;
    }
    const seq = ++loadSeqRef.current;
    setLoading(true);
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
    setDetail(jobDetail);
    setProblems(nextProblems);
    setLoading(false);
  }, [jobId]);

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

  useEffect(() => {
    void loadJobDetail();
    void loadWorkflow();
  }, [loadJobDetail, loadWorkflow]);

  useEffect(() => {
    setCurrentPage(1);
    setSelectedProblemId(null);
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
        <p className="text-sm font-medium text-slate-700">该任务尚未分析完成，暂无审核内容。</p>
        <p className="text-xs text-slate-500">请等待分析结束后再进入审核工作台；可在处理队列查看实时进度。</p>
        <Link href={"/queue" as Route} className="mt-2 text-sm font-medium text-primary-600 hover:text-primary-700">
          前往处理队列
        </Link>
      </div>
    );
  }

  const headerBadge = resolveWorkbenchHeaderBadge(detail);
  const filename = String(detail.filename ?? jobId);
  const reportId = String(detail.structured_report_id ?? "").trim();

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
            </div>
            <div className="mt-0.5 text-xs text-slate-400" data-testid="gbc-review-header-meta">
              {reportId ? `报告 ${reportId} · ` : ""}
              {totalPages !== null ? `${totalPages} 页` : "页数未知"}
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
            onClick={() => router.push("/queue" as Route)}
            disabled={statusCounts.pending > 0}
            data-testid="gbc-review-complete"
            title={statusCounts.pending > 0 ? "还有待处理问题，无法标记复核完成" : undefined}
          >
            完成复核
          </Button>
        </div>
      </div>

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
            {(
              [
                { id: "issues" as const, label: "审核问题" },
                { id: "metadata" as const, label: "元数据" },
                { id: "stages" as const, label: "阶段记录" },
              ]
            ).map((tab) => (
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
          已确认 {statusCounts.confirmed} · 已忽略 {statusCounts.ignored} · 待处理 {statusCounts.pending}
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
