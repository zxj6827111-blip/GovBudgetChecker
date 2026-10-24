/**
 * WorkbenchQueueTable：原型图「处理队列」表格（Task 4.2；前置修复 2 拆分耗时/页数列）。
 *
 * 列：文档（文件名 + 组织·年份·类型副行）/ 当前阶段（阶段名+进度条）/
 * 质量状态（徽章）/ 耗时 / 页数 / 问题数 / 操作。
 *
 * 关键约束：
 * - 阶段与进度必须用 Task 3 的 stage_progress（resolve_stage_progress 的输出），
 *   经 StageProgress 组件渲染，未知态由该组件内部的 isProgressUnknown 处理，
 *   本文件不重新判定 null——避免出现"两处判断逻辑，一处漏改就产生分裂"。
 * - 质量状态徽章：review_required 必须显示"需要人工复核"，不可显示"分析完成"。
 * - 问题数：与审核台采用 review_issue_total，包含待人工确认的材料问题；
 *   提示和系统复核事项单独计数。
 * - 年份未识别显示"未识别到"，不显示 2000 或留空猜测。
 * - 耗时：读 job.elapsed_ms（runtime.collect_job_summary 已按 finished_at-started_at
 *   优先、elapsed_ms.total 兜底的口径计算好），null/undefined 时显示"—"，
 *   不得显示 0 或估算值（真实历史数据实测耗时可用比例见交付说明）。
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Route } from "next";
import { useEffect, useRef, type ReactNode } from "react";
import { ClipboardCheck, RefreshCw } from "lucide-react";

import { Badge, StageProgress, Td, Th } from "@/components/ui";
import AnalysisCheckProgress from "@/components/AnalysisCheckProgress";
import type { BadgeTone } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { normalizeUiTaskStatus } from "@/lib/uiAdapters";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";

import { formatElapsedText, formatPagesText, resolveIssueCountText, resolveFindingCountDetails, resolveReviewEntryDecision } from "./workbenchAdapters";

interface StageProgressPayload {
  phase?: string | null;
  phase_label?: string | null;
  percent?: number | null;
  raw_stage?: string | null;
}

function readStageProgress(job: JobSummaryRecord): StageProgressPayload | null {
  const raw = job.stage_progress;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as StageProgressPayload;
  }
  return null;
}

/** 阶段名文案：优先用 Task 3 的 phase_label，缺失时退回原始 stage 文本，都没有则"—"。
 *  uploaded（待分析）任务的原始 stage 是 "uploaded"，必须翻译成中文"待分析"，
 *  不得让英文原始值或"分析阶段"类暗示在跑的文案出现。 */
function resolveStageLabel(job: JobSummaryRecord): string {
  if (normalizeUiTaskStatus(job.status) === "pending_analysis") {
    return "待分析";
  }
  const stageProgress = readStageProgress(job);
  return stageProgress?.phase_label || String(job.stage ?? "").trim() || "—";
}

/**
 * 质量状态徽章的 tone + 文案。
 *
 * review_required 必须走独立分支显示"需要人工复核"——严禁跟随 completed 分支
 * 显示"分析完成"（任务书明确写出的红线用例）。
 */
function resolveQualityBadge(job: JobSummaryRecord): { tone: BadgeTone; label: string } {
  const status = normalizeUiTaskStatus(job.status);
  // uploaded（已上传未分析）是静止态：严禁显示成"正在分析"（任务书修复 3 红线）。
  if (status === "pending_analysis") {
    return { tone: "neutral", label: "待分析" };
  }
  if (status === "review_required") {
    return { tone: "review", label: "需要人工复核" };
  }
  if (status === "failed") {
    return { tone: "failed", label: "处理失败" };
  }
  if (status === "analyzing") {
    return { tone: "processing", label: "正在分析" };
  }
  // completed：quality_status=degraded 时仍需要可见的降级标记，不可粉饰成纯粹的"分析完成"
  if (job.quality_status === "degraded") {
    return { tone: "lowconf", label: "低置信度" };
  }
  return { tone: "done", label: "分析完成" };
}

function resolveStageTone(job: JobSummaryRecord): "primary" | "success" | "warning" | "danger" | "info" {
  const status = normalizeUiTaskStatus(job.status);
  if (status === "pending_analysis") {
    return "info";
  }
  if (status === "failed") {
    return "danger";
  }
  if (job.quality_status === "degraded") {
    return "warning";
  }
  if (status === "review_required") {
    return "warning";
  }
  return "primary";
}

function formatYearLabel(reportYear: JobSummaryRecord["report_year"]): string {
  if (typeof reportYear === "number" && Number.isFinite(reportYear) && reportYear > 0) {
    return `${reportYear}`;
  }
  return "未识别到";
}

function formatDocTypeLabel(job: JobSummaryRecord): string {
  if (job.report_kind === "budget") {
    return "部门预算";
  }
  if (job.report_kind === "final") {
    return "部门决算";
  }
  return "待复核";
}

export interface WorkbenchQueueTableProps {
  jobs: JobSummaryRecord[];
  /** 报告目录默认折叠检查明细，保留阶段与质量状态，避免每行占用过多高度。 */
  collapsibleChecks?: boolean;
  onReanalyze?: (jobId: string) => void;
  /**
   * 修复 3：可选的「开始分析」回调——只对 uploaded（待分析）态的行渲染入口，
   * 与修复 2 的上传触发分析走同一条链路（POST /api/analyze/{job_id}）。
   */
  onStartAnalysis?: (jobId: string) => void;
  /**
   * 可选：自定义操作列内容（Task 8.2 任务历史页用它在操作列渲染
   * 报告下载入口）。不传时显示明确的"重新分析"按钮（调用 onReanalyze）。
   */
  renderRowActions?: (job: JobSummaryRecord) => ReactNode;
  selection?: {
    selectedIds: ReadonlySet<string>;
    canSelect: (job: JobSummaryRecord) => boolean;
    onToggleJob: (jobId: string) => void;
    onTogglePage: (jobIds: string[], selected: boolean) => void;
    disabled?: boolean;
  };
  actionsDisabled?: boolean;
  busyJobIds?: ReadonlySet<string>;
  reviewReturnTo?: string;
}

/**
 * 「复核」入口（修复 B）：三栏审核台此前只能手敲 URL 到达，全仓没有任何
 * 页面链接到 /review?job=。此组件被工作台总览、处理队列、任务历史三个列表
 * 共用，在这里加入口即可三屏全覆盖。
 *
 * 交互选择：行内链接按钮而非整行点击——文件名链接已承载 /queue?job= 的
 * 高亮/筛选语义（必须保留），整行点击会让两个导航语义互相踩踏；显式按钮
 * 可见性最好、可键盘访问、还能对"未分析完成"的任务干净地禁用并说明原因。
 */
function ReviewEntryLink({ job, returnTo }: { job: JobSummaryRecord; returnTo: string }) {
  const jobId = String(job.job_id ?? "");
  const decision = resolveReviewEntryDecision(job);
  if (!decision.canEnter) {
    return (
      <button
        type="button"
        disabled
        title={decision.reason ?? undefined}
        aria-label={`任务 ${job.filename ?? jobId} 暂不能进入人工复核：${decision.reason ?? "当前不可复核"}`}
        data-testid={`gbc-workbench-queue-review-${jobId}`}
        className="inline-flex cursor-not-allowed items-center gap-1.5 whitespace-nowrap rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-slate-300"
      >
        <ClipboardCheck className="h-3.5 w-3.5" aria-hidden="true" />
        暂不可复核
      </button>
    );
  }
  return (
    <Link
      href={reviewDetailPath(jobId, returnTo) as Route}
      aria-label={`进入任务 ${job.filename ?? jobId} 的人工复核工作台`}
      title="进入人工复核工作台，查看问题、填写复核意见并完成复核"
      data-testid={`gbc-workbench-queue-review-${jobId}`}
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
    >
      <ClipboardCheck className="h-3.5 w-3.5" aria-hidden="true" />
      查看复核材料
    </Link>
  );
}

export function WorkbenchQueueTable({
  jobs,
  collapsibleChecks = false,
  onReanalyze,
  onStartAnalysis,
  renderRowActions,
  selection,
  actionsDisabled = false,
  busyJobIds,
  reviewReturnTo,
}: WorkbenchQueueTableProps) {
  const pathname = usePathname() ?? "/queue";
  const selectAllRef = useRef<HTMLInputElement>(null);
  const selectableIds = selection ? jobs.filter(selection.canSelect).map((job) => String(job.job_id)) : [];
  const selectedPageCount = selectableIds.filter((id) => selection?.selectedIds.has(id)).length;
  const allSelected = selectableIds.length > 0 && selectedPageCount === selectableIds.length;
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedPageCount > 0 && !allSelected;
  }, [selectedPageCount, allSelected]);
  if (jobs.length === 0) {
    return (
      <div
        className="rounded-card border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500"
        data-testid="gbc-workbench-queue-empty"
      >
        没有符合当前筛选条件的任务。
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-card border border-border">
      <table className="w-full border-collapse text-left" data-testid="gbc-workbench-queue-table">
        <thead>
          <tr>
            {selection ? (
              <Th className="w-10">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  aria-label="选择本页可运行任务"
                  checked={allSelected}
                  disabled={selection.disabled || selectableIds.length === 0}
                  onChange={(event) => selection.onTogglePage(selectableIds, event.target.checked)}
                  data-testid="gbc-queue-select-page"
                  className="h-4 w-4 accent-primary-600"
                />
              </Th>
            ) : null}
            <Th>文档</Th>
            <Th>当前阶段</Th>
            <Th>质量状态</Th>
            <Th>耗时</Th>
            <Th>页数</Th>
            <Th>问题 / 提示</Th>
            <Th className="text-right">操作</Th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const stageProgress = readStageProgress(job);
            const qualityBadge = resolveQualityBadge(job);
            const jobId = String(job.job_id ?? "");
            const issueCountText = resolveIssueCountText(job);
            const isPendingAnalysis = normalizeUiTaskStatus(job.status) === "pending_analysis";
            const checkTracks = job.check_progress as { local?: unknown; ai?: unknown } | null | undefined;
            return (
              <tr key={jobId} data-testid={`gbc-workbench-queue-row-${jobId}`} className="hover:bg-surface-100">
                {selection ? (
                  <Td>
                    <input
                      type="checkbox"
                      aria-label={`选择任务 ${job.filename ?? jobId}`}
                      checked={selection.selectedIds.has(jobId)}
                      disabled={selection.disabled || !selection.canSelect(job)}
                      title={!selection.canSelect(job) ? "正在运行或状态未识别的任务不能重复提交" : undefined}
                      onChange={() => selection.onToggleJob(jobId)}
                      data-testid={`gbc-queue-select-${jobId}`}
                      className="h-4 w-4 accent-primary-600"
                    />
                  </Td>
                ) : null}
                <Td>
                  <Link
                    href={`/queue?job=${encodeURIComponent(jobId)}` as Route}
                    className="font-medium text-slate-900 hover:text-primary-700"
                    data-testid={`gbc-workbench-queue-row-link-${jobId}`}
                  >
                    {String(job.filename ?? jobId)}
                  </Link>
                  <div className="mt-0.5 text-xs text-slate-400">
                    {String(job.organization_name ?? "未关联单位")} · {formatYearLabel(job.report_year)} ·{" "}
                    {formatDocTypeLabel(job)}
                  </div>
                </Td>
                <Td>
                  <StageProgress
                    stageLabel={resolveStageLabel(job)}
                    progress={stageProgress?.percent ?? null}
                    tone={resolveStageTone(job)}
                    data-testid={`gbc-workbench-queue-stage-${jobId}`}
                  />
                  {collapsibleChecks && (checkTracks?.local || checkTracks?.ai) ? (
                    <details className="mt-2 text-xs text-slate-500" data-testid={`gbc-report-check-details-${jobId}`}>
                      <summary className="cursor-pointer rounded text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">检查详情</summary>
                      <AnalysisCheckProgress value={job.check_progress} compact />
                    </details>
                  ) : <AnalysisCheckProgress value={job.check_progress} compact />}
                </Td>
                <Td>
                  <Badge tone={qualityBadge.tone}>{qualityBadge.label}</Badge>
                </Td>
                <Td data-testid={`gbc-workbench-queue-elapsed-${jobId}`}>{formatElapsedText(job.elapsed_ms)}</Td>
                <Td data-testid={`gbc-workbench-queue-pages-${jobId}`}>{formatPagesText(job)}</Td>
                <Td data-testid={`gbc-workbench-queue-findings-${jobId}`}>
                  <div className="whitespace-nowrap font-medium">
                    {issueCountText === "—" ? "—" : `${issueCountText} 问题`}
                  </div>
                  {resolveFindingCountDetails(job).map((text) => (
                    <div key={text} className="mt-0.5 whitespace-nowrap text-xs text-slate-500">{text}</div>
                  ))}
                </Td>
                <Td className="text-right">
                  <span className="inline-flex items-center justify-end gap-2">
                    <ReviewEntryLink job={job} returnTo={reviewReturnTo ?? pathname} />
                    {isPendingAnalysis && onStartAnalysis ? (
                      <button
                        type="button"
                        onClick={() => onStartAnalysis(jobId)}
                        disabled={actionsDisabled || busyJobIds?.has(jobId)}
                        aria-label={`开始分析任务 ${jobId}`}
                        data-testid={`gbc-workbench-queue-start-${jobId}`}
                        className="rounded-md border border-border px-2 py-1 text-xs font-medium text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        开始分析
                      </button>
                    ) : null}
                    {renderRowActions ? (
                      renderRowActions(job)
                    ) : (
                      <button
                        type="button"
                        onClick={() => onReanalyze?.(jobId)}
                        disabled={!onReanalyze || actionsDisabled || busyJobIds?.has(jobId) || normalizeUiTaskStatus(job.status) === "analyzing"}
                        aria-label={`重新分析任务 ${job.filename ?? jobId}`}
                        title="重新运行本地检查，并按系统当前设置决定是否调用大模型"
                        data-testid={`gbc-workbench-queue-actions-${jobId}`}
                        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                        重新分析
                      </button>
                    )}
                  </span>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default WorkbenchQueueTable;
