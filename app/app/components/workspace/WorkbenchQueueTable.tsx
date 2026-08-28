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
 * - 问题数：直接读 merged_issue_total（后端 _partition_findings 已按
 *   is_formal_finding 过滤，是 count_formal_findings 同一口径），不在前端自己
 *   用 len(issues) 另算。
 * - 年份未识别显示"未识别到"，不显示 2000 或留空猜测。
 * - 耗时：读 job.elapsed_ms（runtime.collect_job_summary 已按 finished_at-started_at
 *   优先、elapsed_ms.total 兜底的口径计算好），null/undefined 时显示"—"，
 *   不得显示 0 或估算值（真实历史数据实测耗时可用比例见交付说明）。
 */
"use client";

import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";

import { Badge, StageProgress, Td, Th } from "@/components/ui";
import type { BadgeTone } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { normalizeUiTaskStatus } from "@/lib/uiAdapters";

import { formatElapsedText, formatPagesText } from "./workbenchAdapters";

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

/** 阶段名文案：优先用 Task 3 的 phase_label，缺失时退回原始 stage 文本，都没有则"—"。 */
function resolveStageLabel(job: JobSummaryRecord): string {
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

/** 问题数：merged_issue_total 是唯一口径（对照 count_formal_findings），缺失显示 —。 */
function resolveIssueCountText(job: JobSummaryRecord): string {
  const merged = job.merged_issue_total;
  if (typeof merged === "number" && Number.isFinite(merged)) {
    return String(merged);
  }
  const legacy = job.issue_total;
  if (typeof legacy === "number" && Number.isFinite(legacy)) {
    return String(legacy);
  }
  return "—";
}

export interface WorkbenchQueueTableProps {
  jobs: JobSummaryRecord[];
  onReanalyze: (jobId: string) => void;
  /**
   * 可选：自定义操作列内容（Task 8.2 任务历史页用它在操作列渲染
   * 报告下载入口）。不传时保持默认的"更多操作"按钮（调用 onReanalyze），
   * 工作台/处理队列页的行为不变。
   */
  renderRowActions?: (job: JobSummaryRecord) => ReactNode;
}

export function WorkbenchQueueTable({
  jobs,
  onReanalyze,
  renderRowActions,
}: WorkbenchQueueTableProps) {
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
            <Th>文档</Th>
            <Th>当前阶段</Th>
            <Th>质量状态</Th>
            <Th>耗时</Th>
            <Th>页数</Th>
            <Th>问题数</Th>
            <Th className="text-right">操作</Th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const stageProgress = readStageProgress(job);
            const qualityBadge = resolveQualityBadge(job);
            const jobId = String(job.job_id ?? "");
            return (
              <tr key={jobId} data-testid={`gbc-workbench-queue-row-${jobId}`} className="hover:bg-surface-100">
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
                </Td>
                <Td>
                  <Badge tone={qualityBadge.tone}>{qualityBadge.label}</Badge>
                </Td>
                <Td data-testid={`gbc-workbench-queue-elapsed-${jobId}`}>{formatElapsedText(job.elapsed_ms)}</Td>
                <Td>{formatPagesText(job)}</Td>
                <Td>{resolveIssueCountText(job)}</Td>
                <Td className="text-right">
                  {renderRowActions ? (
                    renderRowActions(job)
                  ) : (
                    <button
                      type="button"
                      onClick={() => onReanalyze(jobId)}
                      aria-label={`对任务 ${jobId} 执行操作`}
                      data-testid={`gbc-workbench-queue-actions-${jobId}`}
                      className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                    >
                      <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                    </button>
                  )}
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
