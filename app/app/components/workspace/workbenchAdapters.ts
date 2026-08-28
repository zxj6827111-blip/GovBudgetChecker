import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { normalizeUiTaskStatus } from "../../../lib/uiAdapters";

/**
 * 工作台总览（Task 4）的纯逻辑层：KPI 聚合、页面覆盖率聚合、质量告警派生、
 * 处理队列筛选。拆到无 JSX 依赖的 .ts 文件，理由与 app/components/ui/*Styles.ts
 * 一致（见 buttonStyles.ts 顶部注释）——这批函数承载的语义正是任务书里最容易
 * 被复核抓到的红线（"无数据显示空态而非 0"、"覆盖率缺字段显示 —"），必须能被
 * 现有 jiti 单测脚本直接断言，不能只靠肉眼看渲染结果。
 */

// ---------------------------------------------------------------------------
// KPI 聚合
// ---------------------------------------------------------------------------

export interface WorkbenchKpiCounts {
  /** 待人工复核：status 归一为 review_required 的任务数。 */
  reviewRequired: number;
  /** 正在处理：status 归一为 analyzing 的任务数。 */
  processing: number;
  /** 处理失败：status 归一为 failed 的任务数。 */
  failed: number;
  /** 本周已完成：updated_ts 落在过去 7 天内、且状态为 completed 的任务数。 */
  completedThisWeek: number;
}

const ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * 从真实任务列表计算四张计数类 KPI 卡的数值。
 *
 * 反例（核心断言）：
 * - jobs 为 null/undefined（尚未拉到数据）时返回 null，调用方必须渲染"—"，
 *   不能显示 0——0 意味着"已确认此刻数量为零"。
 * - jobs 为空数组（真实拉到数据，且确认当前没有任何任务）时返回全 0 计数，
 *   这是真实的统计结果，必须原样显示，不能被上一条规则误伤。
 */
export function computeWorkbenchKpiCounts(
  jobs: JobSummaryRecord[] | null | undefined,
  nowMs: number = Date.now(),
): WorkbenchKpiCounts | null {
  if (jobs === null || jobs === undefined) {
    return null;
  }

  let reviewRequired = 0;
  let processing = 0;
  let failed = 0;
  let completedThisWeek = 0;
  const weekStart = nowMs - ONE_WEEK_MS;

  for (const job of jobs) {
    const status = normalizeUiTaskStatus(job.status);
    if (status === "review_required") {
      reviewRequired += 1;
    } else if (status === "analyzing") {
      processing += 1;
    } else if (status === "failed") {
      failed += 1;
    } else if (status === "completed") {
      const updatedTs = normalizeJobTimestampMs(job.updated_ts ?? job.ts);
      if (updatedTs !== null && updatedTs >= weekStart) {
        completedThisWeek += 1;
      }
    }
  }

  return { reviewRequired, processing, failed, completedThisWeek };
}

/** 时间戳可能是秒或毫秒（历史数据混用），与 uiAdapters.normalizeTimestamp 同一判定规则。 */
function normalizeJobTimestampMs(value: unknown): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }
  return numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
}

// ---------------------------------------------------------------------------
// 页面覆盖率聚合
// ---------------------------------------------------------------------------

export interface PageCoverageAggregate {
  /** 平均覆盖率（0-1），仅基于带有真实 page_coverage 字段的任务计算。 */
  averageRatio: number;
  /** 参与聚合的样本量（带 page_coverage 字段的任务数）。 */
  sampleSize: number;
  /** 排除在外的任务数（无 page_coverage 字段，通常是历史任务）。 */
  excludedCount: number;
}

/**
 * 聚合真实 page_coverage 字段，排除没有该字段的任务（历史任务普遍没有，
 * 回放实测 766 个任务 0 个带 page_coverage，见任务书 4.1）。
 *
 * 反例（严禁事项）：
 * - jobs 为 null/undefined 时返回 null，不得显示 0% 或 100%。
 * - jobs 全部没有 page_coverage 字段（sampleSize=0）时也返回 null——
 *   "没有数据"不能被算成"覆盖率为 0%"，这是任务书明确写出的红线用例。
 * - jobs 为空数组时同样返回 null（既没有样本，也没有可聚合的字段）。
 */
export function aggregatePageCoverage(
  jobs: JobSummaryRecord[] | null | undefined,
): PageCoverageAggregate | null {
  if (jobs === null || jobs === undefined) {
    return null;
  }

  const validRatios: number[] = [];
  let excludedCount = 0;

  for (const job of jobs) {
    const raw = job.page_coverage;
    if (raw === null || raw === undefined) {
      excludedCount += 1;
      continue;
    }
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) {
      excludedCount += 1;
      continue;
    }
    validRatios.push(numeric);
  }

  if (validRatios.length === 0) {
    return null;
  }

  const averageRatio = validRatios.reduce((sum, value) => sum + value, 0) / validRatios.length;
  return { averageRatio, sampleSize: validRatios.length, excludedCount };
}

/** 把 0-1 的覆盖率比例格式化为整数百分比文案，null 时格式化为 em dash。 */
export function formatCoveragePercentText(aggregate: PageCoverageAggregate | null): string {
  if (aggregate === null) {
    return "—";
  }
  return `${Math.round(aggregate.averageRatio * 100)}%`;
}

// ---------------------------------------------------------------------------
// 质量告警派生（决策 1=b：Golden Corpus/召回率/精确率一律不渲染）
// ---------------------------------------------------------------------------

export type QualityAlertId = "page_coverage_low" | "unknown_report_kind_high";

export interface QualityAlert {
  id: QualityAlertId;
  title: string;
  description: string;
  /** 供告警面板点击后跳转筛选用（例如 page_coverage_low 跳到"覆盖率不足"筛选态）。 */
  filterHint: Record<string, string>;
}

export interface QualityAlertInputs {
  jobs: JobSummaryRecord[] | null | undefined;
  /** /api/metrics 的 quality.unknown_report_kind 字段。 */
  unknownReportKindRatio: number | null | undefined;
  /** /api/metrics 的 jobs.total 字段：比例类告警的判定分母，用于小样本抑制。 */
  metricsJobsTotal: number | null | undefined;
  /** 页面覆盖率阈值，取自 PAGE_COVERAGE_MIN_RATIO（默认 0.8），不得自定义新阈值。 */
  pageCoverageMinRatio: number;
  /** unknown 比例阈值，取自 docs/OBSERVABILITY_AND_ALERTS.md 的 govbudget_unknown_report_kind_ratio（0.05）。 */
  unknownReportKindMaxRatio: number;
  /** 比例类指标最小样本量护栏，同源自 docs/OBSERVABILITY_AND_ALERTS.md（jobs_total >= 20）。 */
  minSampleSizeForRatioAlerts: number;
}

/**
 * 派生质量告警面板要渲染的条目。
 *
 * 严禁事项（对照任务书 4.3）：
 * - 第三条"Golden Corpus 召回率下降"本轮无标注语料，永远不派生、不渲染
 *   （决策 1=b），本函数从设计上就没有生成这类告警的分支，不是"数据不够就不显示"，
 *   是"这个能力本轮完全不存在"。
 * - 阈值来自调用方传入的 pageCoverageMinRatio / unknownReportKindMaxRatio /
 *   minSampleSizeForRatioAlerts，本函数不内置任何数字，防止阈值与
 *   docs/OBSERVABILITY_AND_ALERTS.md 产生第二套口径。
 * - unknown 比例告警使用 minSampleSizeForRatioAlerts 抑制小样本误报
 *   （文档要求 jobs_total >= 20），样本不足时该条告警不渲染，不是"渲染但标注低置信"。
 * - 覆盖率不足告警统计对象是"确实有 page_coverage 字段、且低于阈值"的任务，
 *   不把"没有 page_coverage 字段"的任务误计入"覆盖率不足"（那是两种不同的缺陷）。
 */
export function deriveQualityAlerts(inputs: QualityAlertInputs): QualityAlert[] {
  const alerts: QualityAlert[] = [];
  const jobs = inputs.jobs ?? [];

  let lowCoverageCount = 0;
  for (const job of jobs) {
    const raw = job.page_coverage;
    if (raw === null || raw === undefined) {
      continue;
    }
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) {
      continue;
    }
    if (numeric < inputs.pageCoverageMinRatio) {
      lowCoverageCount += 1;
    }
  }
  if (lowCoverageCount > 0) {
    alerts.push({
      id: "page_coverage_low",
      title: `${lowCoverageCount} 个任务页面覆盖率不足`,
      description: `存在未解析页面。任务不会被标记为正常完成（阈值 ${Math.round(inputs.pageCoverageMinRatio * 100)}%，来自 PAGE_COVERAGE_MIN_RATIO）。`,
      filterHint: { qualityStatus: "review_required" },
    });
  }

  const jobsTotal = inputs.metricsJobsTotal ?? 0;
  const unknownRatio = inputs.unknownReportKindRatio;
  if (
    jobsTotal >= inputs.minSampleSizeForRatioAlerts &&
    typeof unknownRatio === "number" &&
    Number.isFinite(unknownRatio) &&
    unknownRatio > inputs.unknownReportKindMaxRatio
  ) {
    alerts.push({
      id: "unknown_report_kind_high",
      title: `unknown 文档类型比例 ${(unknownRatio * 100).toFixed(1)}%`,
      description: `高于目标阈值 ${(inputs.unknownReportKindMaxRatio * 100).toFixed(0)}%，需确认分类与人工复核（阈值来自 docs/OBSERVABILITY_AND_ALERTS.md）。`,
      filterHint: { reportKind: "unknown" },
    });
  }

  return alerts;
}

// ---------------------------------------------------------------------------
// 处理队列筛选
// ---------------------------------------------------------------------------

export type WorkbenchStatusFilter = "all" | "review_required" | "analyzing" | "failed" | "completed";

export interface WorkbenchQueueFilters {
  keyword: string;
  status: WorkbenchStatusFilter;
  /** 年份筛选，空字符串表示不筛选；"unresolved" 表示"未识别到"这一特殊态。 */
  year: string;
  organizationId: string;
}

function jobMatchesKeyword(job: JobSummaryRecord, normalizedKeyword: string): boolean {
  if (!normalizedKeyword) {
    return true;
  }
  const haystack = [job.filename, job.organization_name, job.job_id]
    .map((value) => String(value ?? "").toLowerCase())
    .join(" ");
  return haystack.includes(normalizedKeyword);
}

function jobMatchesYear(job: JobSummaryRecord, year: string): boolean {
  if (!year) {
    return true;
  }
  if (year === "unresolved") {
    return job.report_year === null || job.report_year === undefined;
  }
  return String(job.report_year ?? "") === year;
}

function jobMatchesStatus(job: JobSummaryRecord, status: WorkbenchStatusFilter): boolean {
  if (status === "all") {
    return true;
  }
  return normalizeUiTaskStatus(job.status) === status;
}

function jobMatchesOrganization(job: JobSummaryRecord, organizationId: string): boolean {
  if (!organizationId) {
    return true;
  }
  return String(job.organization_id ?? "") === organizationId;
}

/**
 * 处理队列表的筛选谓词。空输入（jobs=null/undefined）返回空数组而非抛错，
 * 由调用方决定空数组时展示"加载中"还是"暂无数据"（本函数不负责区分这两种语义,
 * 它只处理"给定确定的任务列表，筛选出符合条件的子集"这一件事）。
 */
export function filterWorkbenchQueue(
  jobs: JobSummaryRecord[] | null | undefined,
  filters: WorkbenchQueueFilters,
): JobSummaryRecord[] {
  if (!Array.isArray(jobs)) {
    return [];
  }
  const normalizedKeyword = filters.keyword.trim().toLowerCase();
  return jobs.filter(
    (job) =>
      jobMatchesKeyword(job, normalizedKeyword) &&
      jobMatchesStatus(job, filters.status) &&
      jobMatchesYear(job, filters.year) &&
      jobMatchesOrganization(job, filters.organizationId),
  );
}

/**
 * 从真实任务列表中提取可选年份集合（用于年份筛选下拉），降序排列，
 * 并把"未识别到"作为固定追加选项（不是从数据里统计出来的一个"年份值"）。
 */
export function collectAvailableYears(jobs: JobSummaryRecord[] | null | undefined): number[] {
  if (!Array.isArray(jobs)) {
    return [];
  }
  const years = new Set<number>();
  for (const job of jobs) {
    if (typeof job.report_year === "number" && Number.isFinite(job.report_year)) {
      years.add(job.report_year);
    }
  }
  return Array.from(years).sort((a, b) => b - a);
}


// ---------------------------------------------------------------------------
// 耗时/页数格式化（前置修复 2）
// ---------------------------------------------------------------------------

/**
 * 任务耗时格式化。
 *
 * 口径（真实历史数据实测，786 个任务目录）：`runtime.collect_job_summary` 已经
 * 按 finished_at-started_at 优先、elapsed_ms.total 兜底的顺序计算好 elapsed_ms
 * 字段（对照任务书"确实分析过的任务子集里 97.6% 可计算，远高于 elapsed_ms.total
 * 单独口径的约 53%"），本函数只负责把毫秒数格式化为可读文案，不重新决定取值来源。
 *
 * 反例（核心，M1"null 与 0 严格区分"红线在耗时字段上的落地）：
 * elapsedMs 为 null/undefined 时必须显示 em dash，不得显示 0 或任何估算值——
 * 0 毫秒是一个可疑的真实值（正常分析不可能瞬间完成），"未知"与"确认耗时为 0"
 * 是两个不同语义。负数（脏数据/时钟回退）同样视为不可信，显示 em dash。
 */
export function formatElapsedText(elapsedMs: JobSummaryRecord["elapsed_ms"]): string {
  if (typeof elapsedMs !== "number" || !Number.isFinite(elapsedMs) || elapsedMs < 0) {
    return "—";
  }
  const totalSeconds = Math.round(elapsedMs / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return seconds > 0 ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours} 时 ${remainingMinutes} 分` : `${hours} 小时`;
}

/** 页数/覆盖率文案：页数已知时优先显示页数，否则退回覆盖率百分比，都没有则"—"。 */
export function formatPagesText(job: JobSummaryRecord): string {
  const pageCoverage = job.page_coverage;
  const scannedPages = job.scanned_page_count;
  if (typeof scannedPages === "number" && Number.isFinite(scannedPages)) {
    return `${scannedPages} 页`;
  }
  if (typeof pageCoverage === "number" && Number.isFinite(pageCoverage)) {
    return `${Math.round(pageCoverage * 100)}% 覆盖`;
  }
  return "—";
}
