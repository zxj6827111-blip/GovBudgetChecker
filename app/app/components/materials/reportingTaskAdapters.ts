export type ReportingComponent = "all" | "budget" | "final";
export type ReportingView = "overview" | "departments" | "subjects" | "issues" | "leads" | "extras" | "roster" | "remediation";
export type ReportingTask = { id: string; reporting_year: number; jurisdiction_org_id: string; components: { fiscal_year: number; report_kind: "budget" | "final" }[]; draft_components?: { fiscal_year: number; report_kind: "budget" | "final" }[]; state: "draft" | "active" | "archived"; draft_revision: number; active_revision: number | null };
export type ReportingRegion = { id: string; name: string; level: string; partial_scope: boolean; task: ReportingTask | null };
export type ReportingMetrics = { expected: number | null; uploaded: number | null; missing: number | null; overdue_missing: number | null; due_unknown_missing: number | null; not_due_missing: number | null; coverage_rate: number | null; department_count: number | null; unit_count: number | null; empty_departments: number | null; partial_departments: number | null; complete_units: number | null; analyzed: number; reviewed: number; confirmed: number | null; pending: number | null; pending_checks: number | null; unknown_analysis: number; failed: number; not_applicable: number; excluded: number; observed_uploaded?: number; extra_uploaded?: number | null; unassigned_materials?: number; missing_references?: number };
export type ReportingRow = Omit<Partial<ReportingMetrics>, "uploaded"> & { id?: string; name?: string; slot_id?: string | null; organization_id?: string; organization_name?: string; department_id?: string; department_name?: string; fiscal_year?: number; report_kind?: string; uploaded?: number | boolean | null; overdue?: boolean | null; confirmed_count?: number | null; pending_count?: number | null; review_status?: string; status?: string; job_id?: string | null; document_version_id?: number | null; due_at?: string | null; decision?: string; relationship?: string; reason?: string; title?: string; severity?: string; issue_id?: string; rounds_count?: number; history_incomplete?: boolean };
export type ReportingStatistics = { task: ReportingTask; region_name: string; summary: ReportingMetrics; departments: (ReportingMetrics & {id: string; name: string})[]; components: { fiscal_year: number; report_kind: "budget" | "final"; summary: ReportingMetrics }[]; list: {items: ReportingRow[]; page: number; page_size: number; total: number; total_pages: number}; meta: {baseline_ready: boolean; expected_materials_ready: boolean; data_basis?: "organization_catalog" | "confirmed_expected_list" | "existing_slots_only"; roster_published?: boolean; roster_revision: number | null; generated_at: string; partial_scope: boolean; baseline_by?: string; baseline_note?: string; baseline_at?: string; catalog_changed: boolean} };
export type RosterRow = {key: string; subject_org_id: string; subject_org_name: string; subject_kind: string; material_scope: string; caliber: string; jurisdiction_org_id: string; department_org_id: string | null; department_name: string | null; fiscal_year: number; report_kind: string; decision: "pending" | "required" | "not_applicable" | "excluded"; reason: string; due_at: string | null; catalog_missing?: boolean};
export type RosterData = {task: ReportingTask; items: RosterRow[]; catalog_changed: boolean; versions: {roster_revision: number; fiscal_year: number; report_kind: string; confirmed_by: string; note: string; created_at: string}[]};
export type RosterPreview = {valid: boolean; errors: {key:string; reason:string}[]; preview_token:string; required:number; not_applicable:number; excluded:number; due_unknown:number; draft_revision:number};

export function reportingCount(value: number | null | undefined) { return value == null ? "—" : String(value); }
export function reportingRatio(value: number | null | undefined) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
export function reportingYear(value: string | null, currentYear = new Date().getFullYear()) { return value && /^20\d{2}$/.test(value) && Number(value) >= 2001 ? Number(value) : currentYear; }
export function reportingLink(path: string, current: URLSearchParams, fields: Record<string, string> = {}) {
  const params = new URLSearchParams(current);
  params.delete("page");
  for (const [key, value] of Object.entries(fields)) { if (value) params.set(key, value); else params.delete(key); }
  return `${path}?${params}`;
}
export function localDeadline(value: string) { return value ? `${value}:00+08:00` : null; }
export function deadlineInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() + 8 * 3600_000).toISOString().slice(0,16);
}
export async function reportingRequest<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {...options, cache:"no-store", headers:{"Content-Type":"application/json", ...options?.headers}});
  const body = await response.json();
  if (!response.ok || body.ok === false) {
    const detail = body.detail;
    const details = detail?.errors?.slice(0,5).map((e:{reason?:string})=>e.reason).join("；");
    throw new Error(typeof detail === "string" ? detail : [detail?.message || "年度报送请求失败", details].filter(Boolean).join("："));
  }
  return body.data as T;
}
