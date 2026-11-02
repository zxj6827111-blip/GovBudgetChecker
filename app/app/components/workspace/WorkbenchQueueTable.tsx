/**
 * WorkbenchQueueTable：原型图「处理队列」表格（Task 4.2；前置修复 2 拆分耗时/页数列）。
 *
 * 列：文档（文件名 + 组织·年份·类型副行）/ 状态（质量徽章 + 进行中的阶段进度 +
 * 本地/大模型检查一行摘要）/ 页数·耗时 / 复核条目 / 操作。
 *
 * 每行只说一次：已结束的任务不再画“质量门禁 100%”和两条 100% 进度条——
 * 徽章说结论，检查摘要说两条检查线各跑了多少（“本地 89/89 · 大模型 未启用”），
 * 单位、模型与步骤放在悬停提示里。进行中/失败的任务才显示阶段进度条。
 * 窄单元格一律不换行，避免“需/要/人/工/复/核”式的逐字竖排。
 *
 * 关键约束：
 * - 阶段与进度必须用 Task 3 的 stage_progress（resolve_stage_progress 的输出），
 *   经 StageProgress 组件渲染，未知态由该组件内部的 isProgressUnknown 处理，
 *   本文件不重新判定 null——避免出现"两处判断逻辑，一处漏改就产生分裂"。
 * - 质量状态徽章：review_required 必须显示"需要人工复核"，不可显示"分析完成"。
 * - 复核条目：与审核台两个页签同口径，写“必须处理 N / 参考 M”（后端 queue_* 计数），
 *   参考再按审核台“结果类型”列出待核实、证据不足、提示、复核事项；旧摘要没有
 *   queue_* 时退回“N 问题 / 提示 / 复核事项”。
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
import type { BadgeTone } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus } from "@/lib/uiAdapters";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";

import {
  formatElapsedText,
  formatPagesText,
  resolveFindingCountDetails,
  resolveFindingCountSummary,
  resolveIssueCountText,
  resolveQueueCounts,
  resolveReviewEntryDecision,
  summarizeCheckTracks,
  type CheckTrackTone,
} from "./workbenchAdapters";

const CHECK_TONE_CLASSES: Record<CheckTrackTone, string> = {
  done: "text-slate-600",
  active: "text-primary-700",
  warning: "text-warning-700",
  muted: "text-slate-400",
};

interface StageProgressPayload {
  phase?: string | null;
  phase_label?: string | null;
  percent?: number | null;
  raw_stage?: string | null;
}

/** 表头不换行，单元格内边距收窄，让五列在常见笔记本宽度里不出横向滚动。 */
const TH = "whitespace-nowrap px-3";
const TD = "px-3";

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
      查看复核
    </Link>
  );
}

export function WorkbenchQueueTable({
  jobs,
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
              <Th className="w-10 px-3">
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
            <Th className={TH}>文档</Th>
            <Th className={TH}>状态</Th>
            <Th className={TH}>页数 / 耗时</Th>
            <Th className={TH}>复核条目</Th>
            <Th className={`${TH} text-right`}>操作</Th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const stageProgress = readStageProgress(job);
            const qualityBadge = resolveQualityBadge(job);
            const jobId = String(job.job_id ?? "");
            const issueCountText = resolveIssueCountText(job);
            const status = normalizeUiTaskStatus(job.status);
            const isPendingAnalysis = status === "pending_analysis";
            // 已结束的任务阶段必然走到最后，进度条只给进行中和失败（看停在哪一步）。
            const showStage = !isPendingAnalysis && (!isUiTaskFinished(status) || status === "failed");
            const checks = summarizeCheckTracks(job.check_progress);
            const findings = resolveFindingCountSummary(job);
            const queueCounts = resolveQueueCounts(job);
            const meta = `${String(job.organization_name ?? "未关联单位")} · ${formatYearLabel(job.report_year)} · ${formatDocTypeLabel(job)}`;
            return (
              <tr key={jobId} data-testid={`gbc-workbench-queue-row-${jobId}`} className="align-top hover:bg-surface-100">
                {selection ? (
                  <Td className="px-3">
                    <input
                      type="checkbox"
                      aria-label={`选择任务 ${job.filename ?? jobId}`}
                      checked={selection.selectedIds.has(jobId)}
                      disabled={selection.disabled || !selection.canSelect(job)}
                      title={!selection.canSelect(job) ? "正在运行或状态未识别的任务不能重复提交" : undefined}
                      onChange={() => selection.onToggleJob(jobId)}
                      data-testid={`gbc-queue-select-${jobId}`}
                      className="mt-0.5 h-4 w-4 accent-primary-600"
                    />
                  </Td>
                ) : null}
                <Td className={`${TD} min-w-[10rem]`}>
                  <Link
                    href={`/queue?job=${encodeURIComponent(jobId)}` as Route}
                    className="line-clamp-2 font-medium leading-snug text-slate-900 hover:text-primary-700"
                    title={String(job.filename ?? jobId)}
                    data-testid={`gbc-workbench-queue-row-link-${jobId}`}
                  >
                    {String(job.filename ?? jobId)}
                  </Link>
                  <div className="mt-0.5 line-clamp-1 text-xs text-slate-400" title={meta}>{meta}</div>
                </Td>
                <Td className={`${TD} min-w-[8.5rem]`}>
                  <Badge tone={qualityBadge.tone} className="whitespace-nowrap">{qualityBadge.label}</Badge>
                  {showStage ? (
                    <StageProgress
                      stageLabel={resolveStageLabel(job)}
                      progress={stageProgress?.percent ?? null}
                      tone={resolveStageTone(job)}
                      className="mt-1.5 min-w-0"
                      data-testid={`gbc-workbench-queue-stage-${jobId}`}
                    />
                  ) : null}
                  {checks ? (
                    <div className="mt-1 flex flex-wrap gap-x-2 text-xs" title={checks.detail} data-testid={`gbc-workbench-queue-checks-${jobId}`}>
                      {checks.items.map((item) => (
                        <span key={item.key} className={`whitespace-nowrap ${CHECK_TONE_CLASSES[item.tone]}`}>{item.text}</span>
                      ))}
                    </div>
                  ) : null}
                </Td>
                <Td className={`${TD} whitespace-nowrap`}>
                  <div data-testid={`gbc-workbench-queue-pages-${jobId}`}>{formatPagesText(job)}</div>
                  <div className="mt-0.5 text-xs text-slate-400" title="耗时" data-testid={`gbc-workbench-queue-elapsed-${jobId}`}>{formatElapsedText(job.elapsed_ms)}</div>
                </Td>
                {queueCounts ? (
                  // 与审核台两个页签同口径：点进去「必须处理」「参考」页签上的数字就是这两个。
                  <Td className={`${TD} min-w-[7rem]`} data-testid={`gbc-workbench-queue-findings-${jobId}`} title={queueCounts.detail}>
                    <div className={`whitespace-nowrap font-medium ${queueCounts.required > 0 ? "text-warning-700" : ""}`}>必须处理 {queueCounts.required}</div>
                    <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-500">
                      <span className="whitespace-nowrap">参考 {queueCounts.reference}</span>
                      {queueCounts.referenceParts.map((text) => <span key={text} className="whitespace-nowrap text-slate-400">{text}</span>)}
                    </div>
                  </Td>
                ) : (
                <Td className={`${TD} min-w-[7rem]`} data-testid={`gbc-workbench-queue-findings-${jobId}`} title={resolveFindingCountDetails(job).join("；") || undefined}>
                  <div className="whitespace-nowrap font-medium">{issueCountText === "—" ? "—" : `${issueCountText} 问题`}</div>
                  {findings.candidate || findings.others.length > 0 ? (
                    // 每个计数是一个不可拆的词，只在词与词之间换行。
                    <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-500">
                      {findings.candidate ? <span className="whitespace-nowrap text-warning-700">含待核实 {findings.candidate}</span> : null}
                      {findings.others.map((text) => <span key={text} className="whitespace-nowrap">{text}</span>)}
                    </div>
                  ) : null}
                </Td>
                )}
                <Td className={`${TD} text-right`}>
                  <span className="inline-flex flex-col items-end gap-1.5">
                    <ReviewEntryLink job={job} returnTo={reviewReturnTo ?? pathname} />
                    {isPendingAnalysis && onStartAnalysis ? (
                      <button
                        type="button"
                        onClick={() => onStartAnalysis(jobId)}
                        disabled={actionsDisabled || busyJobIds?.has(jobId)}
                        aria-label={`开始分析任务 ${jobId}`}
                        data-testid={`gbc-workbench-queue-start-${jobId}`}
                        className="whitespace-nowrap rounded-md border border-border px-2 py-1 text-xs font-medium text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
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
                        disabled={!onReanalyze || actionsDisabled || busyJobIds?.has(jobId) || status === "analyzing"}
                        aria-label={`重新分析任务 ${job.filename ?? jobId}`}
                        title="重新运行本地检查，并按系统当前设置决定是否调用大模型"
                        data-testid={`gbc-workbench-queue-actions-${jobId}`}
                        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:opacity-50"
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
