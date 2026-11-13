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
  unassigned_total: number;
  material_total: number;
  legacy_total: number;
  activity: { running: number; waiting: number };
}

export function reviewLabel(review: MaterialReview): string {
  if (review.status === "completed") {
    return review.confirmed_count === null ? "已复核 · 问题数未记录"
      : review.confirmed_count === 0 ? "已复核 · 未确认问题" : `已复核 · 确认问题 ${review.confirmed_count} 项`;
  }
  return ({ pending: "待复核", in_progress: "复核中", invalidated: "需重新复核",
    waiting_analysis: "等待分析后复核", history_only: "未归属记录 · 待确认" } as Record<string, string>)[review.status] ?? "复核状态未知";
}

export function versionSource(version: MaterialVersion): string | null {
  if (!version.source_job_id || !version.file_hash || !/^[a-f0-9]{64}$/.test(version.file_hash)) return null;
  return `/api/files/${encodeURIComponent(version.source_job_id)}/source?checksum=${version.file_hash}`;
}

export function materialScopeLabel(value?: string): string {
  return ({ department_summary: "部门汇总", unit_self: "单位本级", government: "政府本级" } as Record<string,string>)[value ?? ""] ?? "材料口径待确认";
}
