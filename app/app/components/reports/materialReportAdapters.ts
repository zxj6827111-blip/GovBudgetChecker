import type { BadgeTone } from "../ui/badgeStyles";

export interface MaterialReview {
  status: string;
  confirmed_count: number | null;
  reviewer?: string | null;
  reviewed_at?: string | null;
}
export interface MaterialRun {
  job_id: string;
  status: string;
  analysis_revision?: number | null;
  created_at?: string | null;
  completed_at?: string | null;
}
export interface MaterialVersion {
  document_version_id: number;
  version_number: number;
  is_current: boolean;
  filename?: string | null;
  file_hash?: string | null;
  created_at?: string | null;
  source_job_id?: string | null;
  current_run?: MaterialRun | null;
  analysis_unavailable?: boolean;
  review: MaterialReview;
  runs?: MaterialRun[];
  reviews?: { status: string; completed_at?: string | null; analysis_job_uuid: string; analysis_basis_token: string; completed_by?: string; confirmed_count?: number | null }[];
}
export interface MaterialReport {
  item_id: string;
  slot_id: string | null;
  job_id?: string | null;
  filename?: string | null;
  original_checksum?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  subject_kind?: string;
  material_scope?: string;
  fiscal_year: number | null;
  report_year: number | null;
  report_kind: string;
  current_version: MaterialVersion | null;
  version_count: number | null;
  analysis_task_count: number;
  mapping_required?: boolean;
  review: MaterialReview;
  previous_review?: { completed_at?: string; completed_by?: string; document_version_id?: number } | null;
  updated_at?: string | null;
}
export interface MaterialReportPage {
  items: MaterialReport[];
  total: number;
  page: number;
  page_size: number;
  page_count: number;
  years: number[];
  has_unknown_year: boolean;
  organization_report_counts: Record<string, number>;
  organization_unit_report_counts?: Record<string, number>;
  uncategorized_total?: number;
  unassigned_total: number;
  material_total: number;
  legacy_total: number;
  /** 目录状态；旧接口没有该字段时目录只显示材料数。 */
  organization_review_status?: Record<string, OrganizationReviewStatus>;
  /** 本范围已登记材料按复核状态计数，各项之和等于 material_total。 */
  review_status_counts?: Record<string, number>;
  activity: { running: number; waiting: number };
}
export interface LatestMaterialStatus {
  fiscal_year: number;
  review_status: string;
  confirmed_count: number | null;
  run_status?: string | null;
}
export type LatestMaterialKey = "department_summary:budget" | "department_summary:final" | "unit_self:budget" | "unit_self:final";
export interface OrganizationReviewStatus {
  review_counts: Record<string, number>;
  unit_review_counts: Record<string, number>;
  latest: Partial<Record<LatestMaterialKey, LatestMaterialStatus>>;
}

/** 尚未完成复核、需要复核人处理的材料：待复核、复核中、需重新复核。 */
export const OPEN_REVIEW_STATUSES = ["pending", "in_progress", "invalidated"] as const;

export function openReviewCount(counts?: Record<string, number> | null): number {
  return OPEN_REVIEW_STATUSES.reduce((sum, status) => sum + (counts?.[status] ?? 0), 0);
}

/** 目录里一份最新材料的简要状态；没有材料时写“暂无”，不推断应交未交。 */
export function directoryMaterialStatus(status?: LatestMaterialStatus | null): { label: string; tone: BadgeTone } {
  if (!status) return { label: "暂无", tone: "neutral" };
  switch (status.review_status) {
    case "completed": return { label: status.confirmed_count === null ? "已复核" : `已复核 · ${status.confirmed_count} 项问题`, tone: "done" };
    case "pending": return { label: "待复核", tone: "review" };
    case "invalidated": return { label: "需重新复核", tone: "review" };
    case "in_progress": return { label: "复核中", tone: "processing" };
    case "waiting_analysis": {
      const run = status.run_status ?? "";
      if (["error", "failed", "cancelled"].includes(run)) return { label: "分析失败", tone: "failed" };
      return !run || run === "uploaded" ? { label: "待分析", tone: "neutral" } : { label: "分析中", tone: "processing" };
    }
    default: return { label: "状态未知", tone: "neutral" };
  }
}

export function reviewLabel(review: MaterialReview): string {
  if (review.status === "completed") {
    return review.confirmed_count === null ? "已复核 · 问题数未记录"
      : review.confirmed_count === 0 ? "已复核 · 未确认问题" : `已复核 · 确认问题 ${review.confirmed_count} 项`;
  }
  return ({ pending: "待复核", in_progress: "复核中", invalidated: "需重新复核",
    waiting_analysis: "等待分析后复核", history_only: "未归属记录 · 待确认" } as Record<string, string>)[review.status] ?? "复核状态未知";
}

/** 文件版本、分析与复核记录在材料详情“版本与来源”中查看，返回时回到部门报告。 */
export function materialHistoryHref(slotId: string, returnTo: string): string {
  return `/materials/slots/${encodeURIComponent(slotId)}?${new URLSearchParams({ tab: "versions", returnTo })}`;
}

export function versionSource(version: MaterialVersion): string | null {
  if (!version.source_job_id || !version.file_hash || !/^[a-f0-9]{64}$/.test(version.file_hash)) return null;
  return `/api/files/${encodeURIComponent(version.source_job_id)}/source?checksum=${version.file_hash}`;
}

export function materialScopeLabel(value?: string): string {
  return ({ department_summary: "部门汇总", unit_self: "单位本级", government: "政府本级" } as Record<string,string>)[value ?? ""] ?? "材料口径待确认";
}

export interface MaterialReportDeleteSummary {
  requested_slot_count: number;
  requested_job_count: number;
  deleted_slot_ids: string[];
  reset_baseline_slot_ids: string[];
  updated_slot_ids: string[];
  deleted_version_ids: number[];
  deleted_document_ids: number[];
  deleted_job_ids: string[];
  deleted_review_session_count: number;
  failed?: Array<{ id: string; detail: string }>;
}

export function buildMaterialDeleteTargets(items: MaterialReport[]): {
  slotIds: string[];
  jobIds: string[];
} {
  const slotIds: string[] = [];
  const jobIds: string[] = [];
  const seenSlots = new Set<string>();
  const seenJobs = new Set<string>();

  for (const item of items) {
    if (item.slot_id) {
      if (!seenSlots.has(item.slot_id)) {
        seenSlots.add(item.slot_id);
        slotIds.push(item.slot_id);
      }
      continue;
    }
    const jobId = item.job_id || item.current_version?.source_job_id || "";
    if (jobId && !seenJobs.has(jobId)) {
      seenJobs.add(jobId);
      jobIds.push(jobId);
    }
  }

  return { slotIds, jobIds };
}

export function materialReportDisplayTitle(item: MaterialReport): string {
  const yearText = item.fiscal_year ? `${item.fiscal_year} 年` : "年度待确认 · ";
  const kindText =
    item.report_kind === "final"
      ? "决算"
      : item.report_kind === "budget"
        ? "预算"
        : "类型待确认报告";
  return `${item.organization_name ?? "主体待确认"} · ${yearText}${kindText}`;
}

export function formatMaterialDeleteSuccess(summary: MaterialReportDeleteSummary): string {
  const failedCount = summary.failed?.length ?? 0;
  const succeededCount = Math.max(
    0,
    summary.requested_slot_count + summary.requested_job_count - failedCount,
  );
  const details: string[] = [];
  if (summary.deleted_version_ids.length > 0) {
    details.push(`清理 ${summary.deleted_version_ids.length} 个文件版本`);
  }
  if (summary.deleted_job_ids.length > 0) {
    details.push(`删除 ${summary.deleted_job_ids.length} 个分析任务`);
  }
  if (summary.deleted_review_session_count > 0) {
    details.push(`清理 ${summary.deleted_review_session_count} 条复核记录`);
  }
  if (summary.reset_baseline_slot_ids.length > 0) {
    details.push(`${summary.reset_baseline_slot_ids.length} 项监督基线已重置为待提交`);
  }
  const base = succeededCount > 0 ? `已删除 ${succeededCount} 份报告` : "已完成报告清理";
  if (failedCount > 0) {
    details.push(`另有 ${failedCount} 项未删除`);
  }
  return details.length > 0 ? `${base}（${details.join("，")}）。` : `${base}。`;
}
