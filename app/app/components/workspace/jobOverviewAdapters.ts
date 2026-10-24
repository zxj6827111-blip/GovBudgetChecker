import { countsFromJobs, type JobCoverageAggregate, type JobOverviewQuery, type JobOverviewView } from "../../../lib/jobListViews";
import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { computeDoneCoverageStats, deriveFailureStageDistribution, type DoneCoverageStats } from "../quality/qualityAdapters";
import { organizationReportCountsFromJobs } from "../reports/departmentDirectory";
import { aggregatePageCoverage, type PageCoverageAggregate } from "./workbenchAdapters";
import { buildCockpitTrend, cockpitPageCoverage, lowCoverageJobs } from "./workbenchCockpitAdapters";

/**
 * /api/jobs?view=overview 的本地版本与取值换算。
 *
 * 工作台总览与质量管理页只需要全部任务的汇总，不需要任务明细；后端未升级
 * （/api/jobs 仍返回全量数组）时，用各页原有的统计函数在本地算出同样形状的结果。
 */
export function buildJobOverviewLocally(
  jobs: JobSummaryRecord[],
  query: JobOverviewQuery,
  nowMs: number = Date.now(),
): JobOverviewView {
  const cockpitCoverage = cockpitPageCoverage(jobs)!;
  const numericCoverage = aggregatePageCoverage(jobs);
  const doneCoverage = computeDoneCoverageStats(jobs, query.coverageMin);
  const trend = buildCockpitTrend(jobs, query.trendDays as 7 | 30, nowMs)!;
  return {
    ...countsFromJobs(jobs),
    organization_report_counts: organizationReportCountsFromJobs(jobs),
    unassociated_count: jobs.filter((job) => !String(job.organization_id ?? "").trim()).length,
    page_coverage: {
      average: cockpitCoverage.averageRatio,
      sample_size: cockpitCoverage.sampleSize,
      excluded_count: cockpitCoverage.excludedCount,
    },
    page_coverage_numeric: numericCoverage
      ? { average: numericCoverage.averageRatio, sample_size: numericCoverage.sampleSize, excluded_count: numericCoverage.excludedCount }
      : { average: null, sample_size: 0, excluded_count: jobs.length },
    coverage_min: query.coverageMin,
    low_coverage_count: lowCoverageJobs(jobs, query.coverageMin).length,
    done_coverage: doneCoverage
      ? { sample_size: doneCoverage.sampleSize, low_coverage_count: doneCoverage.lowCoverageCount }
      : null,
    failure_stages: deriveFailureStageDistribution(jobs) ?? [],
    trend: {
      points: trend.points.map(({ date, uploaded, analyzed }) => ({ date, uploaded, analyzed })),
      missing_upload_times: trend.missingUploadTimes,
      missing_analysis_times: trend.missingAnalysisTimes,
    },
  };
}

/** 工作台页面覆盖率（只认 0～1 的数字）。 */
export function cockpitCoverageFromOverview(aggregate: JobCoverageAggregate | undefined) {
  return aggregate
    ? { averageRatio: aggregate.average, sampleSize: aggregate.sample_size, excludedCount: aggregate.excluded_count }
    : null;
}

/** 质量管理页覆盖率卡片：没有样本时为 null（"没有数据"不能显示成 0%）。 */
export function qualityCoverageFromOverview(aggregate: JobCoverageAggregate | undefined): PageCoverageAggregate | null {
  if (!aggregate || aggregate.sample_size === 0 || aggregate.average === null) return null;
  return { averageRatio: aggregate.average, sampleSize: aggregate.sample_size, excludedCount: aggregate.excluded_count };
}

export function doneCoverageFromOverview(view: JobOverviewView | null): DoneCoverageStats | null {
  const stats = view?.done_coverage;
  return stats ? { sampleSize: stats.sample_size, lowCoverageCount: stats.low_coverage_count } : null;
}
