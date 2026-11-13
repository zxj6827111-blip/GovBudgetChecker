import assert from "node:assert/strict";
import { buildCockpitProgress, buildCockpitTrend, buildDepartmentCoverage, cockpitPageCoverage,
  cockpitOverviewCsv, cockpitTimestamp, validCockpitLedger } from "../app/components/workspace/workbenchCockpitAdapters";
import type { MaterialCoverageSummary } from "../app/components/materials/materialLedgerAdapters";
import type { OrganizationFilterOption } from "../app/components/workspace/OrganizationFilterSelect";

const counts = { not_due: 0, missing: 0, uploaded: 0, processing: 1, review_required: 1, reviewing: 1,
  completed: 4, not_applicable: 0, mapping_required: 0, failed: 1 };
const ledger = { slot_total: 8, status_counts: counts } as MaterialCoverageSummary;
assert.equal(buildCockpitProgress(null, null), null);
assert.deepEqual(buildCockpitProgress([], null), { source: "jobs", total: 0, completed: null, review: 0, processing: 0, failed: 0, other: 0, completionRatio: null });
const analysisDone = [{ job_id: "a", status: "done" }, { job_id: "b", status: "review_required" }];
assert.equal(buildCockpitProgress(analysisDone, null)?.completed, null, "分析完成不能冒充人工复核完成");
assert.equal(buildCockpitProgress(analysisDone, ledger)?.completed, 4);
assert.equal(buildCockpitProgress(analysisDone, ledger)?.completionRatio, 0.5);
assert.equal(buildCockpitProgress(analysisDone, ledger)?.review, 2, "复核中必须计入待办");
assert.equal(validCockpitLedger({ ...ledger, slot_total: 9 }), false, "分类与总量不一致时不使用统计");
assert.equal(validCockpitLedger({ ...ledger, status_counts: { ...counts, completed: undefined } } as unknown as MaterialCoverageSummary), false);
assert.equal(buildCockpitProgress(analysisDone, { ...ledger, slot_total: 9 })?.completed, null);
const emptyLedger: MaterialCoverageSummary = { ...ledger, slot_total: 0,
  status_counts: { not_due: 0, missing: 0, uploaded: 0, processing: 0, review_required: 0,
    reviewing: 0, completed: 0, not_applicable: 0, mapping_required: 0, failed: 0 } };
assert.equal(buildCockpitProgress(analysisDone, emptyLedger)?.source, "jobs", "未建档的历史任务不能消失");
assert.equal(buildCockpitProgress([], emptyLedger)?.completed, 0, "真实空台账应显示零，不是未知");

const organizations: OrganizationFilterOption[] = [
  { id: "d1", name: "同名部门", level: "department", parent_id: null, depth: 0 },
  { id: "d2", name: "同名部门", level: "department", parent_id: null, depth: 0 },
  { id: "u1", name: "直属单位", level: "unit", parent_id: "d1", depth: 1 },
  { id: "cycle1", name: "环1", level: "unit", parent_id: "cycle2", depth: 1 },
  { id: "cycle2", name: "环2", level: "unit", parent_id: "cycle1", depth: 1 },
];
const coverage = buildDepartmentCoverage([
  { job_id: "1", organization_id: "u1" }, { job_id: "2", organization_id: "u1" },
  { job_id: "3", organization_name: "同名部门" }, { job_id: "4", organization_id: "cycle1" },
], organizations)!;
assert.equal(coverage.covered, 1);
assert.equal(coverage.total, 2);
assert.equal(coverage.ratio, 0.5);
assert.equal(coverage.unlinkedJobs, 2);
assert.deepEqual(coverage.uncovered.map((org) => org.id), ["d2"]);
assert.deepEqual(coverage.departments.map((org) => [org.id, org.covered]), [["d1", true], ["d2", false]]);
assert.equal(buildDepartmentCoverage([], null), null);
assert.equal(buildDepartmentCoverage([], [])?.ratio, null);
assert.equal(cockpitPageCoverage([{ job_id: "a", page_coverage: 0.9 }, { job_id: "b", page_coverage: 1.1 },
  { job_id: "c", page_coverage: null }, { job_id: "d", page_coverage: NaN }])?.sampleSize, 1);
assert.deepEqual(cockpitPageCoverage([{ job_id: "a", page_coverage: null }]),
  { averageRatio: null, sampleSize: 0, excludedCount: 1 });

const now = new Date(2026, 9, 12, 15).getTime();
const recent = new Date(2026, 9, 12, 9).getTime();
const older = new Date(2026, 9, 2, 9).getTime();
const trendJobs = [
  { job_id: "old", status: "done", created_ts: older / 1000, updated_ts: recent / 1000 },
  { job_id: "current", status: "review_required", created_ts: recent, analysis_finished_ts: recent / 1000 },
  { job_id: "missing", status: "done", updated_ts: recent },
  { job_id: "running", status: "processing", created_ts: recent, analysis_finished_ts: recent },
  { job_id: "future", status: "uploaded", created_ts: now + 500000 },
];
const week = buildCockpitTrend(trendJobs, 7, now)!;
assert.equal(week.points.length, 7);
assert.equal(week.uploadedTotal, 2);
assert.equal(week.analyzedTotal, 1, "仅真实终态分析时间计数，不能借修改时间或旧运行结果");
assert.equal(week.missingUploadTimes, 1);
assert.equal(week.missingAnalysisTimes, 2);
assert.equal(buildCockpitTrend(trendJobs, 30, now)?.uploadedTotal, 3);
assert.equal(buildCockpitTrend(null, 7, now), null);
assert.equal(cockpitTimestamp("1720000000"), null);
assert.equal(cockpitTimestamp(Infinity), null);
const csv = cockpitOverviewCsv(buildCockpitProgress(analysisDone, null), coverage, null, week, 7);
assert.ok(csv.startsWith("\uFEFF"));
assert.ok(csv.includes('"已完成人工复核","—"'));
assert.ok(csv.includes("不代表人工复核完成"));
console.log("workbenchCockpitAdapters.test.ts passed: authoritative review, unknown/zero, organization coverage, real event times, export semantics");
