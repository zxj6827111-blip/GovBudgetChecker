import assert from "node:assert/strict";
import { filterReviewList, nextReviewItem, reviewDetailPath, reviewListFilters, reviewListPath, safeReviewReturnPath, type ReviewListItem } from "../app/components/review-workbench/reviewListAdapters";
import { queueFiltersFromQuery, queueReturnPath } from "../app/components/queue/queuePageAdapters";
import { computeNavBadgeCounts } from "../app/components/workspace/navBadges";
import { hasCompletedReviewForCurrentBasis, resolveReviewBadge, type ReviewLifecycleDataRecord } from "../app/components/review-workbench/reviewLifecycleAdapters";

const item = (job: string, year: number, status: ReviewListItem["review_status"] = "pending"): ReviewListItem => ({
  item_id: `slot:${job}`, job_id: job, slot_id: job, document_version_id: 1,
  filename: `${year}.pdf`, organization_id: "unit", organization_name: "同名单位", department_name: "部门",
  fiscal_year: year, report_kind: "budget", review_status: status, pending_findings: null, pending_checks: null,
  reviewer: null, reviewed_at: null, updated_at: null,
});
const items = [item("a", 2026), item("b", 2025), item("c", 2026, "in_progress"), item("d", 2026, "completed"), item("old", 2026, "history_only")];
const filters = reviewListFilters(new URLSearchParams("year=2026&q=同名&page=2"));
assert.deepEqual(filterReviewList(items, filters).map(i => i.job_id), ["a", "c"]);
assert.equal(nextReviewItem(items, "a", reviewListPath(filters))?.job_id, "c", "下一份仍须符合财政年度与关键词");
assert.equal(nextReviewItem(items, "c", reviewListPath(filters)), null, "不能跨出清单或循环到第一份");
assert.equal(nextReviewItem(items, "a", "/queue"), null);
for (const path of ["https://example.com", "//example.com", "/\\example.com", "/login", "/review/../../login"]) assert.equal(safeReviewReturnPath(path), "/review");
assert.equal(safeReviewReturnPath("/review?job=bad&returnTo=bad&year=2025"), "/review?year=2025");
const detail = new URL(reviewDetailPath("a", reviewListPath(filters)), "http://test");
assert.equal(detail.searchParams.get("returnTo"), reviewListPath(filters));
assert.equal(reviewListFilters(new URLSearchParams("status=bad&page=-2")).status, "todo");
assert.equal(reviewListFilters(new URLSearchParams("page=-2")).page, 1);
const queue = queueFiltersFromQuery(new URLSearchParams("q=单位&year=2025&status=review_required&kind=final&stage=quality_gate&org=x"));
const queueUrl = new URL(queueReturnPath(queue, "updated_desc", 3), "http://test");
assert.deepEqual(queueFiltersFromQuery(queueUrl.searchParams), queue, "返回处理队列必须保留全部筛选条件");
assert.equal(queueUrl.searchParams.get("page"), "3");
assert.deepEqual(computeNavBadgeCounts([{ job_id: "old", status: "review_required" }]), { analyzing: 0 }, "分析状态不能推算人工复核待办");
assert.deepEqual(computeNavBadgeCounts(null, 3), { review_required: 3 });
assert.deepEqual(computeNavBadgeCounts([], 0), { analyzing: 0, review_required: 0 });
const finishedSession = {
  review_session_id: "finished", status: "completed", document_version_id: 2,
  analysis_job_uuid: "a", analysis_basis_token: "a:2", started_by: "reviewer", started_at: "2026-10-12T00:00:00Z",
  completed_by: "reviewer", completed_at: "2026-10-12T01:00:00Z", invalidated_at: null, invalidated_reason: null,
};
const lifecycle: ReviewLifecycleDataRecord = {
  slot_id: "slot", current_document_version_id: 2,
  current_analysis: { job_uuid: "a", analysis_revision: 2, analysis_basis_token: "a:2", document_version_id: 2, status: "done", completed: true, formal_issue_count: 0 },
  current_session: null,
  history: [finishedSession, { ...finishedSession, review_session_id: "old", document_version_id: 1, status: "invalidated", analysis_basis_token: "a:1", invalidated_reason: "analysis_restarted" }],
  completion_gate: { can_complete: false, blockers: [] },
};
assert.equal(resolveReviewBadge(lifecycle)?.label, "已完成复核", "旧失效记录不能覆盖当前已完成状态");
assert.equal(hasCompletedReviewForCurrentBasis({ ...lifecycle, current_document_version_id: 3, current_analysis: { ...lifecycle.current_analysis, document_version_id: 3 } }), false, "同一分析令牌也不能跨文件版本继承完成状态");
console.log("reviewListAdapters.test.ts passed");
