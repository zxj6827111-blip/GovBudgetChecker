export type SupervisorView = "overview" | "materials" | "issues" | "remediation";
export type SupervisorPeriod = { fiscal_year: number; report_kind: "budget" | "final"; material_count: number; uploaded: number };
export type Finding = { issue_id: string; title: string; severity: string; source?: string; slot_id?: string; organization_name?: string; department_name?: string; category?: string; evidence_reference?: string };
export type Material = { slot_id: string; organization_id: string; organization_name: string; department_id?: string; department_name?: string;
  subject_kind: string; fiscal_year: number; report_kind: string; status: string; applicability_status: string;
  uploaded: boolean; document_version_id: number | null; filename?: string; job_id: string | null; basis_token: string | null; review_session_id?: string | null;
  overdue: boolean; due_at: string | null; analyzed: boolean; review_status: string; confirmed_count: number | null; pending_count: number | null; pending_checks: number | null;
  rounds_count: number; version_count: number; history_incomplete: boolean; reviewer?: string; reviewed_at?: string; updated_at?: string };
export type Department = { id: string; name: string; material_count: number; uploaded: number; reviewed: number; confirmed: number;
  severe: number; pending: number; expected: number | null; received_expected: number | null; missing: number | null; empty: boolean | null; partial: boolean | null;
  repeat_materials: number; max_rounds: number; problem_rate: number | null };
export type SupervisorData = {
  summary: { material_count: number; uploaded: number; expected: number | null; received_expected: number | null; missing: number | null;
    empty_departments: number | null; partial_departments: number | null; confirmed: number; pending: number; pending_checks: number; unknown_coverage: number; unknown_analysis: number;
    reviewed: number; analyzed: number; failed: number; overdue_missing: number; repeat_materials: number; unregistered_history: number;
    department_count: number; unit_count: number; categories: Array<{ name: string; count: number }> };
  ranking: Department[]; departments: Array<{ id: string; name: string }>; priority: Material[];
  list: { items: Array<Material | Department | Finding>; page: number; page_size: number; total: number; total_pages: number };
  meta: { baseline_ready: boolean; baseline_by?: string; baseline_at?: string; baseline_preview_token?: string;
    data_basis: string; scope_id: string; fiscal_year: number; report_kind: string; generated_at: string; is_read_only: boolean;
    organizations: Array<{ id: string; name: string; level: string; parent_id: string | null }>; available_periods?: SupervisorPeriod[] };
};
export type ReviewSnapshot = { session_id: string; document_version_id: number; version_number?: number; basis_token: string; reviewed_at: string; reviewer: string;
  confirmed_count: number; findings: Finding[] | null; snapshot_complete: boolean };
export type RectificationRound = { id: string; round_number: number; document_version_id: number; feedback_session_id: string; submitted_by: string;
  submitted_at: string; note: string; verification: { fixed: number; remaining: number; new: number; note: string; basis_token: string; review_session_id?: string } | null;
  verified_at: string | null; verified_by: string | null };
export type MaterialDetail = { material: Material; findings: Finding[]; reviews: ReviewSnapshot[]; rounds: RectificationRound[]; is_read_only: boolean };

export function displayCount(value: number | null | undefined): string { return value == null ? "—" : String(value); }

export function defaultSupervisorPeriod(params: URLSearchParams, periods: SupervisorPeriod[]): Record<string, string> | null {
  const year = params.get("fiscal_year"), kind = params.get("report_kind");
  // 明确指定的筛选必须保留，包括用户主动查看尚未报送的年度。
  if (year && kind) return null;
  const candidates = periods.filter(p => p.material_count > 0 && (!year || p.fiscal_year === Number(year)) && (!kind || p.report_kind === kind));
  candidates.sort((a, b) => b.fiscal_year - a.fiscal_year || (a.report_kind === "budget" ? 0 : 1) - (b.report_kind === "budget" ? 0 : 1));
  const latest = candidates[0];
  if (!latest) return null;
  return { ...(!year ? { fiscal_year: String(latest.fiscal_year) } : {}), ...(!kind ? { report_kind: latest.report_kind } : {}) };
}
export function percent(value: number | null | undefined): string { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
export function missingDepartments(data: SupervisorData): number | null {
  const { empty_departments: a, partial_departments: b } = data.summary;
  return a == null || b == null ? null : a + b;
}
export function reportRatio(data: SupervisorData): number | null {
  return data.meta.baseline_ready && data.summary.expected ? data.summary.received_expected! / data.summary.expected : null;
}
export function labelReview(value: string): string {
  return ({ completed: "复核完成", in_progress: "复核中", pending: "待复核", waiting_analysis: "待分析/处理中" } as Record<string, string>)[value] || "状态待确认";
}
export function severityLabel(value: string): string {
  return ({ critical: "严重", high: "高", medium: "中", low: "低", manual_review: "人工补核" } as Record<string, string>)[value] || "未分级";
}
export function organizationPath(id: string, organizations: SupervisorData["meta"]["organizations"]): string {
  const index = new Map(organizations.map(o => [o.id, o])), names: string[] = [], seen = new Set<string>();
  let current = index.get(id);
  while (current && !seen.has(current.id)) { seen.add(current.id); names.unshift(current.name); current = current.parent_id ? index.get(current.parent_id) : undefined; }
  return names.join(" / ");
}
export function readSupervisorData(payload: unknown): SupervisorData {
  const value = payload as { ok?: boolean; data?: SupervisorData };
  if (!value?.ok || !value.data?.summary || !Array.isArray(value.data.list?.items) || !Array.isArray(value.data.meta?.organizations)) throw new Error("统计响应不完整");
  return value.data;
}
export function verificationCounts(previous: Finding[], current: Finding[], choices: Record<string, string>) {
  const comparisons = previous.map(issue => ({ previous_issue_id: issue.issue_id, resolution: choices[issue.issue_id] === "fixed" ? "fixed" : "remains",
    current_issue_id: choices[issue.issue_id] === "fixed" ? null : choices[issue.issue_id] || null }));
  const linked = comparisons.map(c => c.current_issue_id).filter(Boolean);
  return { complete: previous.every(p => Boolean(choices[p.issue_id])) && new Set(linked).size === linked.length,
    comparisons, fixed: comparisons.filter(c => c.resolution === "fixed").length, newCount: current.length - linked.length };
}
