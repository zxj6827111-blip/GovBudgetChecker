import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import {
  aggregatePageCoverage,
  collectAvailableYears,
  computeWorkbenchKpiCounts,
  deriveQualityAlerts,
  filterWorkbenchQueue,
  formatCoveragePercentText,
  formatElapsedText,
  formatPagesText,
} from "../app/components/workspace/workbenchAdapters";

// 本文件测试 Task 4 工作台总览的纯逻辑层（KPI 聚合/覆盖率聚合/质量告警派生/
// 队列筛选）。这批函数承载的语义正是任务书里最容易被复核抓到的红线：
// "无数据显示空态而非 0"、"覆盖率缺字段显示 —"、"Golden Corpus 一律不出现"。

// --- computeWorkbenchKpiCounts：核心反例——未拉到数据(null/undefined) 不得显示 0 ----

assert.equal(
  computeWorkbenchKpiCounts(null),
  null,
  "REGRESSION: 未拉到数据时必须返回 null（渲染层据此显示 —），不得返回全 0 计数",
);
assert.equal(computeWorkbenchKpiCounts(undefined), null);

// 空数组是真实拉到数据、且确认当前没有任何任务，必须返回真实的全 0，不是 null
assert.deepEqual(
  computeWorkbenchKpiCounts([]),
  { reviewRequired: 0, processing: 0, failed: 0, completedThisWeek: 0 },
  "REGRESSION: 空数组是真实的零计数，必须显示 0，不能和 null/undefined 混为一谈",
);

const now = new Date("2026-03-15T00:00:00.000Z").getTime();
const withinWeek = now - 3 * 24 * 60 * 60 * 1000; // 3 天前
const beyondWeek = now - 10 * 24 * 60 * 60 * 1000; // 10 天前

const mixedJobs: JobSummaryRecord[] = [
  { job_id: "a", status: "review_required" },
  { job_id: "b", status: "needs_review" },
  { job_id: "c", status: "processing" },
  { job_id: "d", status: "queued" },
  { job_id: "e", status: "failed" },
  { job_id: "f", status: "error" },
  { job_id: "g", status: "done", updated_ts: withinWeek },
  { job_id: "h", status: "completed", updated_ts: withinWeek },
  { job_id: "i", status: "done", updated_ts: beyondWeek }, // 完成但超过本周窗口，不计入
  { job_id: "j", status: "done" }, // 无时间戳，不计入本周完成
];

assert.deepEqual(
  computeWorkbenchKpiCounts(mixedJobs, now),
  { reviewRequired: 2, processing: 2, failed: 2, completedThisWeek: 2 },
  "review_required/needs_review 归入待复核；processing/queued 归入正在处理；" +
    "failed/error 归入处理失败；只有本周窗口内的 completed 才计入本周已完成",
);

// --- aggregatePageCoverage：严禁把"没有数据"算成 0% 或 100% ------------------

assert.equal(aggregatePageCoverage(null), null, "REGRESSION: 未拉到数据不得聚合出任何覆盖率");
assert.equal(aggregatePageCoverage(undefined), null);
assert.equal(
  aggregatePageCoverage([]),
  null,
  "REGRESSION: 空任务列表没有可聚合的覆盖率样本，必须是 null 而非 0",
);

const jobsWithoutCoverageField: JobSummaryRecord[] = [
  { job_id: "legacy-1", status: "done" },
  { job_id: "legacy-2", status: "done", page_coverage: null },
];
assert.equal(
  aggregatePageCoverage(jobsWithoutCoverageField),
  null,
  "REGRESSION: 历史任务全部没有 page_coverage 字段时必须返回 null，不得显示 0% 或 100%（这是任务书明确写出的红线用例，对照766个历史任务0个带page_coverage的回放结论）",
);

const jobsWithMixedCoverage: JobSummaryRecord[] = [
  { job_id: "legacy-1", status: "done" }, // 排除
  { job_id: "new-1", status: "done", page_coverage: 1.0 },
  { job_id: "new-2", status: "done", page_coverage: 0.5 },
];
const aggregate = aggregatePageCoverage(jobsWithMixedCoverage);
assert.ok(aggregate, "有真实 page_coverage 字段时必须返回聚合结果");
assert.equal(aggregate?.sampleSize, 2, "样本量必须只统计带 page_coverage 字段的任务");
assert.equal(aggregate?.excludedCount, 1, "必须如实报告被排除的样本数，供页面显示样本量说明");
assert.equal(aggregate?.averageRatio, 0.75);

assert.equal(formatCoveragePercentText(null), "—", "REGRESSION: 覆盖率缺字段时必须显示 em dash");
assert.notEqual(formatCoveragePercentText(null), "0%", "REGRESSION: 覆盖率缺字段绝不能显示成 0%");
assert.equal(formatCoveragePercentText(aggregate), "75%");

// 真实覆盖率恰好为 0（已确认扫描页占满全篇）时必须显示 0%，不能与"未知"混淆
const zeroCoverageAggregate = aggregatePageCoverage([{ job_id: "z", status: "done", page_coverage: 0 }]);
assert.equal(zeroCoverageAggregate?.sampleSize, 1);
assert.equal(formatCoveragePercentText(zeroCoverageAggregate), "0%", "真实的 0 覆盖率必须显示 0%，不是 —");

// --- deriveQualityAlerts：Golden Corpus 一律不出现；阈值来自调用方传入 --------

const baseAlertInputs = {
  jobs: [] as JobSummaryRecord[],
  unknownReportKindRatio: null as number | null,
  metricsJobsTotal: 0,
  pageCoverageMinRatio: 0.8,
  unknownReportKindMaxRatio: 0.05,
  minSampleSizeForRatioAlerts: 20,
};

assert.deepEqual(
  deriveQualityAlerts(baseAlertInputs),
  [],
  "无数据时必须是空数组（正常空态），不得为了不空而伪造告警",
);

const alertTitles = (alerts: ReturnType<typeof deriveQualityAlerts>) => alerts.map((item) => item.title);

// 反例：不管输入如何组合，都绝不能出现 Golden Corpus / 召回率 / 精确率 相关告警
for (const scenario of [
  baseAlertInputs,
  {
    ...baseAlertInputs,
    jobs: Array.from({ length: 30 }, (_, i) => ({ job_id: `job-${i}`, status: "done", page_coverage: 0.1 })),
    unknownReportKindRatio: 0.9,
    metricsJobsTotal: 30,
  },
]) {
  const alerts = deriveQualityAlerts(scenario);
  for (const alert of alerts) {
    assert.doesNotMatch(alert.title, /Golden Corpus|召回率|精确率/, "REGRESSION: 告警文案绝不能出现未度量的指标（决策1=b）");
    assert.doesNotMatch(alert.description, /Golden Corpus|召回率|精确率/);
  }
}

// 覆盖率不足告警：只统计"确实有 page_coverage 且低于阈值"的任务
const coverageAlertInputs = {
  ...baseAlertInputs,
  jobs: [
    { job_id: "a", status: "done", page_coverage: 0.5 }, // 低于 0.8，计入
    { job_id: "b", status: "done", page_coverage: 0.9 }, // 高于 0.8，不计入
    { job_id: "c", status: "done" }, // 无字段，不计入"覆盖率不足"（那是另一种缺陷）
  ] as JobSummaryRecord[],
};
const coverageAlerts = deriveQualityAlerts(coverageAlertInputs);
assert.equal(coverageAlerts.length, 1);
assert.equal(coverageAlerts[0].id, "page_coverage_low");
assert.match(coverageAlerts[0].title, /^1 个任务页面覆盖率不足$/);

// unknown 比例告警：样本不足（jobs_total < 20）时必须抑制，即便比例已超阈值
const smallSampleInputs = {
  ...baseAlertInputs,
  unknownReportKindRatio: 0.5,
  metricsJobsTotal: 5, // < minSampleSizeForRatioAlerts=20
};
assert.deepEqual(
  deriveQualityAlerts(smallSampleInputs).filter((item) => item.id === "unknown_report_kind_high"),
  [],
  "REGRESSION: jobs_total < 20 时必须抑制比例类告警（阈值同源 docs/OBSERVABILITY_AND_ALERTS.md），不得为凑告警而降低样本量门槛",
);

const sufficientSampleInputs = {
  ...baseAlertInputs,
  unknownReportKindRatio: 0.5,
  metricsJobsTotal: 25,
};
const unknownAlerts = deriveQualityAlerts(sufficientSampleInputs).filter(
  (item) => item.id === "unknown_report_kind_high",
);
assert.equal(unknownAlerts.length, 1, "样本量达标且比例超阈值时必须渲染该告警");
assert.match(unknownAlerts[0].title, /50\.0%/);

// 比例未超阈值时不渲染
const belowThresholdInputs = { ...baseAlertInputs, unknownReportKindRatio: 0.01, metricsJobsTotal: 25 };
assert.deepEqual(
  deriveQualityAlerts(belowThresholdInputs).filter((item) => item.id === "unknown_report_kind_high"),
  [],
);

// --- filterWorkbenchQueue：关键字/状态/年份/组织联动筛选 ---------------------

const queueJobs: JobSummaryRecord[] = [
  {
    job_id: "job-001",
    filename: "2025年市教育局部门预算.pdf",
    organization_name: "市教育局",
    organization_id: "org-edu",
    status: "review_required",
    report_year: 2025,
  },
  {
    job_id: "job-002",
    filename: "2024年市卫健委决算.pdf",
    organization_name: "市卫健委",
    organization_id: "org-health",
    status: "done",
    report_year: 2024,
  },
  {
    job_id: "job-003",
    filename: "扫描件_未知单位.pdf",
    organization_name: undefined,
    organization_id: undefined,
    status: "processing",
    report_year: null, // 年份未识别到
  },
];

assert.deepEqual(
  filterWorkbenchQueue(null, { keyword: "", status: "all", year: "", organizationId: "" }),
  [],
  "未拉到数据时返回空数组（由调用方决定展示加载中还是暂无数据）",
);

assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "教育局", status: "all", year: "", organizationId: "" }).length,
  1,
  "关键字应匹配组织名",
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "job-002", status: "all", year: "", organizationId: "" }).length,
  1,
  "关键字应匹配任务 ID",
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "", status: "review_required", year: "", organizationId: "" }).length,
  1,
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "", status: "all", year: "2024", organizationId: "" }).length,
  1,
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "", status: "all", year: "unresolved", organizationId: "" }).length,
  1,
  "REGRESSION: 年份筛选的 unresolved 态必须精确匹配 report_year 为 null/undefined 的任务，不得误匹配 2000 或其它猜测值",
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "", status: "all", year: "", organizationId: "org-health" }).length,
  1,
);
assert.equal(
  filterWorkbenchQueue(queueJobs, { keyword: "", status: "all", year: "", organizationId: "" }).length,
  3,
  "无筛选条件时返回全部",
);

// --- collectAvailableYears：不得把"未识别到"当作一个具体年份值放进下拉 --------

assert.deepEqual(collectAvailableYears(queueJobs), [2025, 2024], "应降序排列，且不包含 null 年份");
assert.deepEqual(collectAvailableYears(null), []);
assert.ok(
  !collectAvailableYears(queueJobs).includes(2000),
  "REGRESSION: 可选年份集合中绝不能出现 2000 兜底值",
);

// --- formatElapsedText：前置修复 2，null/0/负数/正常值的正反对照 --------------

assert.equal(formatElapsedText(null), "—", "REGRESSION: 耗时缺失(null)必须显示 em dash，不得显示 0 或猜测值");
assert.equal(formatElapsedText(undefined), "—");
assert.notEqual(formatElapsedText(null), "0 秒", "REGRESSION: 耗时缺失绝不能显示成 0 秒");

// 真实的 0 毫秒（如果确实发生）必须显示为 0 秒，不能与"未知"混淆——null 与 0 严格区分
assert.equal(formatElapsedText(0), "0 秒", "真实的 0 毫秒耗时必须显示为 0 秒，不是 —");

assert.equal(formatElapsedText(12500), "13 秒", "12500ms 四舍五入到 13 秒");
assert.equal(formatElapsedText(45000), "45 秒");
assert.equal(formatElapsedText(60000), "1 分钟", "恰好 60 秒应显示为 1 分钟（不带零秒尾巴）");
assert.equal(formatElapsedText(90000), "1 分 30 秒");
assert.equal(formatElapsedText(3600000), "1 小时", "恰好 1 小时应显示为 1 小时（不带零分尾巴）");
assert.equal(formatElapsedText(3660000), "1 时 1 分");

// 反例：负数（脏数据/时钟回退）必须视为不可信，显示 em dash 而不是负数文案
assert.equal(formatElapsedText(-5000), "—", "REGRESSION: 负数耗时（脏数据）必须显示 —，不得显示负数");

// --- formatPagesText：页数优先于覆盖率百分比，都没有则 — --------------------

assert.equal(formatPagesText({ job_id: "a", scanned_page_count: 48 } as JobSummaryRecord), "48 页");
assert.equal(
  formatPagesText({ job_id: "b", page_coverage: 0.75 } as JobSummaryRecord),
  "75% 覆盖",
  "无页数字段时应退回覆盖率百分比",
);
assert.equal(
  formatPagesText({ job_id: "c" } as JobSummaryRecord),
  "—",
  "REGRESSION: 页数与覆盖率字段都缺失时必须显示 —，不得显示 0 页",
);
// 两者都有时页数优先（更精确的真实数据优先于聚合百分比）
assert.equal(
  formatPagesText({ job_id: "d", scanned_page_count: 30, page_coverage: 0.9 } as JobSummaryRecord),
  "30 页",
);

console.log("workbenchAdapters.test.ts passed");
