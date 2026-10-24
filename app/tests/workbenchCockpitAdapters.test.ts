import assert from "node:assert/strict";
import { buildCockpitProgress, buildCockpitTaskPageLocally, buildCockpitTrend, buildDepartmentCoverage, cockpitPageCoverage,
  cockpitOverviewCsv, cockpitTaskQuery, cockpitTimestamp, cockpitTrendFromDaily, sameCockpitTaskKey,
  validCockpitLedger, type CockpitTaskKey } from "../app/components/workspace/workbenchCockpitAdapters";
import { buildJobOverviewLocally, cockpitCoverageFromOverview, doneCoverageFromOverview,
  qualityCoverageFromOverview } from "../app/components/workspace/jobOverviewAdapters";
import { deriveQualityAlerts } from "../app/components/workspace/workbenchAdapters";
import { organizationReportCountsFromJobs } from "../app/components/reports/departmentDirectory";
import type { MaterialCoverageSummary } from "../app/components/materials/materialLedgerAdapters";
import type { OrganizationFilterOption } from "../app/components/workspace/OrganizationFilterSelect";
import { countsFromJobs, jobOverviewSearchParams, jobPageSearchParams } from "../lib/jobListViews";
import type { JobSummaryRecord } from "../lib/uiAdapters";

/** 页面现在拿到的是接口汇总；测试用本地同口径汇总代替。 */
const summarize = (jobs: JobSummaryRecord[]) => ({ ...countsFromJobs(jobs), organization_report_counts: organizationReportCountsFromJobs(jobs) });

const counts = { not_due: 0, missing: 0, uploaded: 0, processing: 1, review_required: 1, reviewing: 1,
  completed: 4, not_applicable: 0, mapping_required: 0, failed: 1 };
const ledger = { slot_total: 8, status_counts: counts } as MaterialCoverageSummary;
assert.equal(buildCockpitProgress(null, null), null);
assert.deepEqual(buildCockpitProgress(summarize([]), null), { source: "jobs", total: 0, completed: null, review: 0, processing: 0, failed: 0, other: 0, completionRatio: null });
const analysisDone = [{ job_id: "a", status: "done" }, { job_id: "b", status: "review_required" }];
assert.equal(buildCockpitProgress(summarize(analysisDone), null)?.completed, null, "分析完成不能冒充人工复核完成");
assert.equal(buildCockpitProgress(summarize(analysisDone), ledger)?.completed, 4);
assert.equal(buildCockpitProgress(summarize(analysisDone), ledger)?.completionRatio, 0.5);
assert.equal(buildCockpitProgress(summarize(analysisDone), ledger)?.review, 2, "复核中必须计入待办");
assert.equal(validCockpitLedger({ ...ledger, slot_total: 9 }), false, "分类与总量不一致时不使用统计");
assert.equal(validCockpitLedger({ ...ledger, status_counts: { ...counts, completed: undefined } } as unknown as MaterialCoverageSummary), false);
assert.equal(buildCockpitProgress(summarize(analysisDone), { ...ledger, slot_total: 9 })?.completed, null);
const emptyLedger: MaterialCoverageSummary = { ...ledger, slot_total: 0,
  status_counts: { not_due: 0, missing: 0, uploaded: 0, processing: 0, review_required: 0,
    reviewing: 0, completed: 0, not_applicable: 0, mapping_required: 0, failed: 0 } };
assert.equal(buildCockpitProgress(summarize(analysisDone), emptyLedger)?.source, "jobs", "未建档的历史任务不能消失");
assert.equal(buildCockpitProgress(summarize([]), emptyLedger)?.completed, 0, "真实空台账应显示零，不是未知");

const organizations: OrganizationFilterOption[] = [
  { id: "d1", name: "同名部门", level: "department", parent_id: null, depth: 0 },
  { id: "d2", name: "同名部门", level: "department", parent_id: null, depth: 0 },
  { id: "u1", name: "直属单位", level: "unit", parent_id: "d1", depth: 1 },
  { id: "cycle1", name: "环1", level: "unit", parent_id: "cycle2", depth: 1 },
  { id: "cycle2", name: "环2", level: "unit", parent_id: "cycle1", depth: 1 },
];
const coverage = buildDepartmentCoverage(summarize([
  { job_id: "1", organization_id: "u1" }, { job_id: "2", organization_id: "u1" },
  { job_id: "3", organization_name: "同名部门" }, { job_id: "4", organization_id: "cycle1" },
]), organizations)!;
assert.equal(coverage.covered, 1);
assert.equal(coverage.total, 2);
assert.equal(coverage.ratio, 0.5);
assert.equal(coverage.unlinkedJobs, 2);
assert.deepEqual(coverage.uncovered.map((org) => org.id), ["d2"]);
assert.deepEqual(coverage.departments.map((org) => [org.id, org.covered]), [["d1", true], ["d2", false]]);
assert.equal(buildDepartmentCoverage(summarize([]), null), null);
assert.equal(buildDepartmentCoverage(summarize([]), [])?.ratio, null);
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
const csv = cockpitOverviewCsv(buildCockpitProgress(summarize(analysisDone), null), coverage, null, week, 7);
assert.ok(csv.startsWith("\uFEFF"));
assert.ok(csv.includes('"已完成人工复核","—"'));
assert.ok(csv.includes("不代表人工复核完成"));
// --- 接口汇总（view=overview）与本地统计等价 -------------------------------------------
const overviewJobs: JobSummaryRecord[] = [...trendJobs,
  { job_id: "low", status: "done", page_coverage: 0.5, organization_id: "u1" },
  { job_id: "ok", status: "done", page_coverage: 0.95 },
  { job_id: "invalid", status: "error", page_coverage: 1.4, stage_failed_at: { phase: "pdf_parse", phase_label: "PDF 解析" } },
];
const overview = buildJobOverviewLocally(overviewJobs, { coverageMin: 0.8, trendDays: 30 }, now);
assert.equal(overview.trend.points.length, 30);
assert.deepEqual(cockpitTrendFromDaily(overview.trend, 7), buildCockpitTrend(overviewJobs, 7, now),
  "接口按天返回 30 天，截取近 7 日必须与直接按 7 日统计一致（含标签）");
assert.equal(cockpitTrendFromDaily(null, 7), null);
assert.deepEqual(cockpitCoverageFromOverview(overview.page_coverage), { averageRatio: 0.725, sampleSize: 2, excludedCount: 6 });
assert.deepEqual(qualityCoverageFromOverview(overview.page_coverage_numeric)?.sampleSize, 3, "质量页口径：任何有限数值都纳入");
assert.equal(qualityCoverageFromOverview({ average: null, sample_size: 0, excluded_count: 3 }), null, "无样本不能显示成 0%");
assert.deepEqual(doneCoverageFromOverview(overview), { sampleSize: 2, lowCoverageCount: 1 });
assert.equal(overview.low_coverage_count, 1);
assert.deepEqual(overview.failure_stages, [{ phase: "pdf_parse", label: "PDF 解析", count: 1 }]);
assert.equal(overview.unassociated_count, 7);
assert.equal(deriveQualityAlerts({ lowCoverageCount: overview.low_coverage_count, unknownReportKindRatio: null,
  metricsJobsTotal: 0, pageCoverageMinRatio: 0.8, unknownReportKindMaxRatio: 0.05, minSampleSizeForRatioAlerts: 20 })[0]?.title,
"1 个任务页面覆盖率不足", "告警计数与覆盖率不足明细同一口径");
const overviewParams = jobOverviewSearchParams({ coverageMin: 0.8, trendDays: 30 }, "Asia/Shanghai");
assert.equal(overviewParams.toString(), "view=overview&coverage_min=0.8&trend_days=30&tz=Asia%2FShanghai");

// --- 抽屉任务明细按页读取 ------------------------------------------------------------
const taskKey: CockpitTaskKey = { session: 1, lowCoverage: true, keyword: "", status: "all", year: "", organizationId: "", page: 1 };
assert.equal(jobPageSearchParams(cockpitTaskQuery(taskKey, 0.8)).toString(), "view=page&page=1&page_size=20&coverage_below=0.8");
assert.equal(jobPageSearchParams(cockpitTaskQuery({ ...taskKey, lowCoverage: false, status: "failed" }, 0.8)).get("coverage_below"), null);
assert.ok(sameCockpitTaskKey(taskKey, { ...taskKey }));
assert.ok(!sameCockpitTaskKey(taskKey, { ...taskKey, session: 2 }), "重新打开抽屉必须重新取数");
const lowPage = buildCockpitTaskPageLocally([
  { job_id: "low", status: "done", page_coverage: 0.5 }, { job_id: "ok", status: "done", page_coverage: 0.95 },
  { job_id: "none", status: "done" },
], taskKey, 0.8);
assert.deepEqual(lowPage.items.map((job) => job.job_id), ["low"]);
assert.equal(lowPage.job_total, 3);
console.log("workbenchCockpitAdapters.test.ts passed: authoritative review, unknown/zero, organization coverage, real event times, export semantics, overview parity, paged drawer");
