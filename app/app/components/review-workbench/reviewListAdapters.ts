export type ReviewListStatus = "pending" | "in_progress" | "invalidated" | "completed" | "waiting_analysis" | "history_only";
export type ReviewListFilter = "todo" | "all" | ReviewListStatus;
export interface ReviewListItem {
  item_id: string;
  slot_id: string | null;
  job_id: string | null;
  document_version_id: number | null;
  filename: string | null;
  organization_id: string | null;
  organization_name: string | null;
  department_name: string | null;
  fiscal_year: number | null;
  report_kind: string;
  review_status: ReviewListStatus;
  pending_findings: number | null;
  pending_checks: number | null;
  reviewer: string | null;
  reviewed_at: string | null;
  updated_at: string | null;
}
export interface ReviewListData {
  items: ReviewListItem[];
  counts: Record<ReviewListStatus, number>;
  pending_total: number;
  viewer: string;
}
export interface ReviewListFilters { status: ReviewListFilter; keyword: string; year: string; organization: string; kind: string; page: number; }
export const REVIEW_LIST_LABELS: Record<ReviewListFilter, string> = {
  todo: "待办", pending: "待复核", in_progress: "复核中", invalidated: "需重新复核",
  completed: "已完成", waiting_analysis: "等待分析", history_only: "历史结果（只读）", all: "全部",
};
const PRIORITY: Record<ReviewListStatus, number> = { invalidated: 0, pending: 1, in_progress: 2, completed: 3, waiting_analysis: 4, history_only: 5 };
export function isReviewTodo(item: ReviewListItem) { return ["pending", "in_progress", "invalidated"].includes(item.review_status); }
export function reviewListFilters(params: Pick<URLSearchParams, "get">): ReviewListFilters {
  const status = params.get("status") ?? "todo";
  return {
    status: Object.hasOwn(REVIEW_LIST_LABELS, status) ? status as ReviewListFilter : "todo",
    keyword: params.get("q") ?? "", year: params.get("year") ?? "",
    organization: params.get("org") ?? "", kind: params.get("kind") ?? "all",
    page: Math.max(1, Math.floor(Number(params.get("page")) || 1)),
  };
}
export function reviewListPath(filters: ReviewListFilters): string {
  const params = new URLSearchParams();
  if (filters.status !== "todo") params.set("status", filters.status);
  if (filters.keyword) params.set("q", filters.keyword);
  if (filters.year) params.set("year", filters.year);
  if (filters.organization) params.set("org", filters.organization);
  if (filters.kind !== "all") params.set("kind", filters.kind);
  if (filters.page > 1) params.set("page", String(filters.page));
  return `/review${params.size ? `?${params}` : ""}`;
}
export function filterReviewList(items: ReviewListItem[], filters: ReviewListFilters): ReviewListItem[] {
  const keyword = filters.keyword.trim().toLocaleLowerCase();
  return items.filter(item => (
    (filters.status === "all" || (filters.status === "todo" ? isReviewTodo(item) : item.review_status === filters.status))
    && (!filters.year || (filters.year === "unknown" ? item.fiscal_year === null : String(item.fiscal_year) === filters.year))
    && (!filters.organization || item.organization_id === filters.organization)
    && (filters.kind === "all" || item.report_kind === filters.kind)
    && (!keyword || [item.filename, item.organization_name, item.department_name, item.fiscal_year, item.job_id]
      .join(" ").toLocaleLowerCase().includes(keyword))
  )).sort((a, b) => PRIORITY[a.review_status] - PRIORITY[b.review_status]
    || (Date.parse(b.updated_at ?? "") || 0) - (Date.parse(a.updated_at ?? "") || 0)
    || a.item_id.localeCompare(b.item_id));
}
/** 返回地址只接受站内列表入口，拒绝外部地址与递归详情参数。 */
export function safeReviewReturnPath(value: string | null | undefined): string {
  if (!value?.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/review";
  try {
    const url = new URL(value, "http://gbc.local");
    if (url.origin !== "http://gbc.local" || !/^\/(review|queue|materials|reports|history|workbench|archive)(\/|$)/.test(url.pathname)) return "/review";
    if (url.pathname === "/review") { url.searchParams.delete("job"); url.searchParams.delete("returnTo"); }
    return `${url.pathname}${url.search}`;
  } catch { return "/review"; }
}
export function reviewDetailPath(jobId: string, returnTo = "/review"): string {
  return `/review?${new URLSearchParams({ job: jobId, returnTo: safeReviewReturnPath(returnTo) })}`;
}
export function nextReviewItem(items: ReviewListItem[], jobId: string, returnTo: string): ReviewListItem | null {
  const path = safeReviewReturnPath(returnTo);
  const url = new URL(path, "http://gbc.local");
  if (url.pathname !== "/review") return null;
  const filtered = filterReviewList(items, reviewListFilters(url.searchParams)).filter(item => item.job_id);
  const current = filtered.findIndex(item => item.job_id === jobId);
  return current >= 0 ? filtered[current + 1] ?? null : filtered.find(item => item.job_id !== jobId) ?? null;
}
