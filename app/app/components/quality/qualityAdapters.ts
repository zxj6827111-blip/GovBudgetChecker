import type { JobSummaryRecord } from "../../../lib/uiAdapters";
import { normalizeUiTaskStatus } from "../../../lib/uiAdapters";

/**
 * 质量管理页（Task 7）的纯逻辑层：指标派生、失败阶段分布、结构性门禁判定。
 *
 * 与 workbenchAdapters.ts 同一拆分理由（见其顶部注释）：本文件承载的语义
 * 是任务书里最容易被复核抓到的红线——"分母为 0 显示 — 而非 100%"、
 * "无失败任务不得凭空生成分段"、"门禁只列可机器判定的结构性项"——
 * 必须能被 jiti 单测直接断言，不能只靠肉眼看渲染结果。
 *
 * 识别率 vs 正确性的概念区分（任务书 7.4 的核心判断）：
 * - "识别率/未识别比例"回答"系统识别到了吗"——分母是任务数，无需标准答案；
 * - "准确率/正确率"回答"识别得对吗"——需要人工标注的标准答案才能算，
 *   本轮无标注语料，从未被度量，因此本文件不存在任何产出"准确率"的函数，
 *   页面也不得把识别率标成准确率。
 */

// ---------------------------------------------------------------------------
// /api/metrics 响应类型（本页消费的子集）
// ---------------------------------------------------------------------------

export interface QualityMetricsResponse {
  jobs?: { total?: number | null };
  quality?: {
    unknown_report_kind?: { count?: number | null; ratio?: number | null };
    review_required?: { count?: number | null; ratio?: number | null };
    unresolved_report_year?: { count?: number | null; ratio?: number | null };
    error_jobs?: { count?: number | null; ratio?: number | null };
    evidence_degraded_findings?: number | null;
    formal_issue_total?: number | null;
    evidence_completeness?: {
      findings_total?: number | null;
      findings_complete?: number | null;
      completeness_rate?: number | null;
      jobs_without_field?: number | null;
    };
  };
  report_id?: { collision_count?: number | null };
}

// ---------------------------------------------------------------------------
// 阈值常量（全部同源自既有文档/代码，本文件不另立口径）
// ---------------------------------------------------------------------------

/**
 * done 任务页面覆盖率下限：与 api/main.py 的 PAGE_COVERAGE_MIN_RATIO 默认值
 * 及 CI 业务门禁 done_jobs_min_page_coverage 同源（0.8）。
 */
export const PAGE_COVERAGE_MIN_RATIO = 0.8;
/** 证据完整率下限：CI 业务门禁 evidence_completeness_rate 阈值（0.99）。 */
export const EVIDENCE_COMPLETENESS_MIN_RATE = 0.99;
/** unknown 类型比例上限：CI 业务门禁 unknown_report_kind_ratio 阈值（0.35）。 */
export const UNKNOWN_REPORT_KIND_MAX_RATIO = 0.35;

// ---------------------------------------------------------------------------
// 处理成功率
// ---------------------------------------------------------------------------

/**
 * 处理成功率 = (任务总数 − error 终态任务数) / 任务总数。
 *
 * 口径（与 docs/OBSERVABILITY_AND_ALERTS.md 的 govbudget_error_job_ratio
 * 是同一对分子分母：该文档定义 error 比例的分母为"被扫描到的任务产物总数"，
 * 处理成功率即其补数）：
 * - 分母：metrics.jobs.total（累计口径，非时间窗口）；
 * - 分子：分母 − metrics.quality.error_jobs.count。
 *
 * 反例（红线）：分母为 0/null（未拉到数据或没有任何任务）时返回 null，
 * 调用方必须显示 "—"——0 个任务时"成功率"没有定义，不能显示 0% 或 100%。
 */
export function computeProcessingSuccessRate(
  metrics: QualityMetricsResponse | null | undefined,
): number | null {
  if (metrics === null || metrics === undefined) {
    return null;
  }
  const total = metrics.jobs?.total;
  const errorCount = metrics.quality?.error_jobs?.count;
  if (typeof total !== "number" || !Number.isFinite(total) || total <= 0) {
    return null;
  }
  if (typeof errorCount !== "number" || !Number.isFinite(errorCount) || errorCount < 0) {
    return null;
  }
  return round4((total - errorCount) / total);
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

/** 把 0-1 比例格式化为一位小数百分比文案；null/undefined 显示 em dash。 */
export function formatRatioPercentText(ratio: number | null | undefined): string {
  if (typeof ratio !== "number" || !Number.isFinite(ratio)) {
    return "—";
  }
  return `${(ratio * 100).toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// 失败阶段分布（Task 3 的 stage_failed_at 真实归因）
// ---------------------------------------------------------------------------

/** 有序阶段枚举（与 src/services/pipeline_stages.py 一致，不含 OCR——
 *  OCR 阶段本轮不存在，枚举里没有，这里也不得为了凑原型图加上）。 */
export const FAILURE_STAGE_ORDER = [
  "upload",
  "pdf_parse",
  "metadata_recognition",
  "rule_ai_analysis",
  "quality_gate",
] as const;

export interface FailureStageBucket {
  /** 规范阶段名；无法归因时为 "unattributed"。 */
  phase: string;
  /** 中文展示名；无法归因时为"未归因"。 */
  label: string;
  count: number;
}

interface StageFailedAtPayload {
  phase?: string | null;
  phase_label?: string | null;
}

function readStageFailedAt(job: JobSummaryRecord): StageFailedAtPayload | null {
  const raw = job.stage_failed_at;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as StageFailedAtPayload;
  }
  return null;
}

/**
 * 从真实任务列表统计失败任务的阶段归因分布。
 *
 * 口径约束：
 * - 只统计 status 归一为 failed 的任务（成功任务 status.json 里可能残留
 *   上一次失败的 stage_failed_at，不能把"失败过后来成功"也算进失败分布）；
 * - 阶段集合只有 5 个规范阶段 + "未归因"（stage_failed_at 缺失或 phase
 *   无法识别时），绝无 OCR；
 * - jobs 为 null/undefined（未拉到数据）时返回 null，调用方显示加载态，
 *   不能和"没有失败任务"（返回空数组）混为一谈。
 */
export function deriveFailureStageDistribution(
  jobs: JobSummaryRecord[] | null | undefined,
): FailureStageBucket[] | null {
  if (jobs === null || jobs === undefined) {
    return null;
  }

  const counts = new Map<string, number>();
  const labels = new Map<string, string>();
  let failedCount = 0;

  for (const job of jobs) {
    if (normalizeUiTaskStatus(job.status) !== "failed") {
      continue;
    }
    failedCount += 1;
    const failedAt = readStageFailedAt(job);
    const phase = typeof failedAt?.phase === "string" ? failedAt.phase : "";
    if (phase && (FAILURE_STAGE_ORDER as readonly string[]).includes(phase)) {
      counts.set(phase, (counts.get(phase) ?? 0) + 1);
      labels.set(phase, String(failedAt?.phase_label ?? phase));
    } else {
      counts.set("unattributed", (counts.get("unattributed") ?? 0) + 1);
      labels.set("unattributed", "未归因");
    }
  }

  if (failedCount === 0) {
    return [];
  }

  const buckets: FailureStageBucket[] = Array.from(counts.entries()).map(([phase, count]) => ({
    phase,
    label: labels.get(phase) ?? phase,
    count,
  }));
  buckets.sort((a, b) => {
    if (b.count !== a.count) {
      return b.count - a.count;
    }
    const orderA = (FAILURE_STAGE_ORDER as readonly string[]).indexOf(a.phase);
    const orderB = (FAILURE_STAGE_ORDER as readonly string[]).indexOf(b.phase);
    // "unattributed" 排在最后
    const indexA = orderA === -1 ? FAILURE_STAGE_ORDER.length : orderA;
    const indexB = orderB === -1 ? FAILURE_STAGE_ORDER.length : orderB;
    return indexA - indexB;
  });
  return buckets;
}

/** 失败阶段条形的相对宽度（0-1，最大分段为 1）；空分布返回空 map。 */
export function computeFailureStageBarRatios(
  buckets: FailureStageBucket[],
): Map<string, number> {
  const result = new Map<string, number>();
  const max = buckets.reduce((acc, item) => Math.max(acc, item.count), 0);
  if (max <= 0) {
    return result;
  }
  for (const bucket of buckets) {
    result.set(bucket.phase, bucket.count / max);
  }
  return result;
}

/** 条形宽度样式（React style 对象）；未知比例（不在 map 里）时为 0 宽。 */
export function computeFailureStageWidthStyle(
  ratios: Map<string, number>,
  phase: string,
): { width: string } {
  const ratio = ratios.get(phase) ?? 0;
  return { width: `${Math.min(100, Math.max(0, ratio * 100))}%` };
}

// ---------------------------------------------------------------------------
// done 任务覆盖率门禁统计（页面覆盖率门禁的判定数据）
// ---------------------------------------------------------------------------

export interface DoneCoverageStats {
  /** 带真实 page_coverage 字段的 done 任务数（样本量）。 */
  sampleSize: number;
  /** 其中覆盖率低于阈值的任务数。 */
  lowCoverageCount: number;
}

/**
 * 统计 done（completed 归一）任务里带 page_coverage 字段的样本与低覆盖数。
 *
 * 口径与 CI 业务门禁 done_jobs_min_page_coverage 一致：只判定"done 且带
 * page_coverage"的任务；没有 page_coverage 字段的历史任务不是本门禁的
 * 判定对象（它们属于 completed_jobs_have_page_coverage 那条门禁，需要
 * 按"任务产生时间"区分新旧，运行时指标无法判定，如实排除）。
 *
 * jobs 为 null/undefined，或没有任何带字段的 done 任务（sampleSize=0）时
 * 返回 null——无样本不是"全部达标"。
 */
export function computeDoneCoverageStats(
  jobs: JobSummaryRecord[] | null | undefined,
  minRatio: number = PAGE_COVERAGE_MIN_RATIO,
): DoneCoverageStats | null {
  if (jobs === null || jobs === undefined) {
    return null;
  }
  let sampleSize = 0;
  let lowCoverageCount = 0;
  for (const job of jobs) {
    if (normalizeUiTaskStatus(job.status) !== "completed") {
      continue;
    }
    const raw = job.page_coverage;
    if (raw === null || raw === undefined) {
      continue;
    }
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) {
      continue;
    }
    sampleSize += 1;
    if (numeric < minRatio) {
      lowCoverageCount += 1;
    }
  }
  if (sampleSize === 0) {
    return null;
  }
  return { sampleSize, lowCoverageCount };
}

// ---------------------------------------------------------------------------
// 组织未关联比例（识别覆盖率指标之一，非正确性度量）
// ---------------------------------------------------------------------------

/**
 * organization_id 为空的任务比例（"是否关联到组织"的统计，不是组织识别得
 * 对不对——后者需要标准答案）。jobs 为 null/undefined 或空数组时返回 null。
 */
export function computeUnassociatedOrgRatio(
  jobs: JobSummaryRecord[] | null | undefined,
): number | null {
  if (jobs === null || jobs === undefined || jobs.length === 0) {
    return null;
  }
  let unassociated = 0;
  for (const job of jobs) {
    const orgId = String(job.organization_id ?? "").trim();
    if (!orgId) {
      unassociated += 1;
    }
  }
  return round4(unassociated / jobs.length);
}

// ---------------------------------------------------------------------------
// 结构性发布门禁（只列可机器判定的项）
// ---------------------------------------------------------------------------

export type StructuralGateStatus = "pass" | "fail" | "no_sample";

export type StructuralGateId =
  | "report_id_uniqueness"
  | "page_coverage"
  | "evidence_completeness"
  | "unknown_report_kind";

export interface StructuralGate {
  id: StructuralGateId;
  title: string;
  status: StructuralGateStatus;
  /** 含阈值与样本量的事实说明（数据从哪来、判了什么、没判什么）。 */
  detail: string;
}

export interface StructuralGateInputs {
  metrics: QualityMetricsResponse | null | undefined;
  doneCoverageStats: DoneCoverageStats | null;
}

/**
 * 派生结构性发布门禁清单（可机器判定的四条，与 CI 业务门禁五条对应：
 * report_id_uniqueness / done_jobs_min_page_coverage / evidence_completeness_rate /
 * unknown_report_kind_ratio；completed_jobs_have_page_coverage 并入页面覆盖率
 * 条目的 detail 说明，因为它需要按任务新旧区分，运行时无法机器判定）。
 *
 * 判定语义：
 * - pass：样本存在且满足阈值；
 * - fail：样本存在且不满足阈值；
 * - no_sample：无样本（如证据完整率分母为 0、没有任何任务）——
 *   "没有数据"绝不能被判成"通过"，那会把空仓库伪装成达标系统。
 *
 * 严禁事项：本函数从设计上就不存在"P0/P1 检出效果"之类基于人工标注的
 * 门禁分支（无标注语料，无法判定，原型图该条不渲染）。
 */
export function deriveStructuralGates(inputs: StructuralGateInputs): StructuralGate[] {
  const metrics = inputs.metrics ?? null;
  const jobsTotal = metrics?.jobs?.total ?? null;
  const hasJobsSample = typeof jobsTotal === "number" && jobsTotal > 0;

  const gates: StructuralGate[] = [];

  // 1. 状态真实性（report_id 唯一性，P0-09 的回归观测口）
  const collisionCount = metrics?.report_id?.collision_count ?? null;
  if (!hasJobsSample) {
    gates.push({
      id: "report_id_uniqueness",
      title: "状态真实性门禁（report_id 唯一性）",
      status: "no_sample",
      detail: "尚无任务产物，无样本可判定。",
    });
  } else if (typeof collisionCount === "number" && collisionCount > 0) {
    gates.push({
      id: "report_id_uniqueness",
      title: "状态真实性门禁（report_id 唯一性）",
      status: "fail",
      detail: `存在 ${collisionCount} 组不同原件共用同一报告身份，需核对组织匹配与年度识别后重跑（阈值：冲突数 = 0）。`,
    });
  } else {
    gates.push({
      id: "report_id_uniqueness",
      title: "状态真实性门禁（report_id 唯一性）",
      status: "pass",
      detail: `未发现不同原件共用同一报告身份（样本 ${jobsTotal} 个任务，阈值：冲突数 = 0）。`,
    });
  }

  // 2. 页面覆盖率（done 任务最低覆盖率，B-01/B-03）
  const coverageStats = inputs.doneCoverageStats;
  if (coverageStats === null) {
    gates.push({
      id: "page_coverage",
      title: "页面覆盖率门禁（done 任务）",
      status: "no_sample",
      detail: "没有带 page_coverage 留痕的已完成任务（历史任务普遍缺失该字段），无样本可判定。",
    });
  } else if (coverageStats.lowCoverageCount > 0) {
    gates.push({
      id: "page_coverage",
      title: "页面覆盖率门禁（done 任务）",
      status: "fail",
      detail: `${coverageStats.lowCoverageCount}/${coverageStats.sampleSize} 个已完成任务覆盖率低于 ${Math.round(PAGE_COVERAGE_MIN_RATIO * 100)}%，本应转人工复核（阈值：低覆盖 done 任务数 = 0）。`,
    });
  } else {
    gates.push({
      id: "page_coverage",
      title: "页面覆盖率门禁（done 任务）",
      status: "pass",
      detail: `带覆盖率留痕的 ${coverageStats.sampleSize} 个已完成任务全部 ≥ ${Math.round(PAGE_COVERAGE_MIN_RATIO * 100)}%（历史任务无留痕，不在判定范围）。`,
    });
  }

  // 3. 证据完整率（P0-07；分母为 0 → no_sample，绝不显示 100%）
  const evidenceRate = metrics?.quality?.evidence_completeness?.completeness_rate ?? null;
  const evidenceTotal = metrics?.quality?.evidence_completeness?.findings_total ?? null;
  const evidenceJobsWithoutField =
    metrics?.quality?.evidence_completeness?.jobs_without_field ?? null;
  const evidenceSuffix =
    typeof evidenceJobsWithoutField === "number" && evidenceJobsWithoutField > 0
      ? `另有 ${evidenceJobsWithoutField} 个任务无证据留痕字段，未纳入判定。`
      : "";
  if (evidenceRate === null || evidenceTotal === null || evidenceTotal <= 0) {
    gates.push({
      id: "evidence_completeness",
      title: "证据完整率门禁",
      status: "no_sample",
      detail: `正式问题证据完整率的分母为 0（无 finding 或无留痕），无样本可判定。${evidenceSuffix}`,
    });
  } else if (evidenceRate >= EVIDENCE_COMPLETENESS_MIN_RATE) {
    gates.push({
      id: "evidence_completeness",
      title: "证据完整率门禁",
      status: "pass",
      detail: `${evidenceTotal} 条 finding 的证据完整率为 ${(evidenceRate * 100).toFixed(1)}%，≥ 99% 阈值。${evidenceSuffix}`,
    });
  } else {
    gates.push({
      id: "evidence_completeness",
      title: "证据完整率门禁",
      status: "fail",
      detail: `证据完整率 ${(evidenceRate * 100).toFixed(1)}% 低于 99% 阈值（${evidenceTotal} 条 finding）。${evidenceSuffix}`,
    });
  }

  // 4. unknown 类型比例（P0-04/P1-05）
  const unknownRatio = metrics?.quality?.unknown_report_kind?.ratio ?? null;
  const unknownCount = metrics?.quality?.unknown_report_kind?.count ?? null;
  if (!hasJobsSample || unknownRatio === null) {
    gates.push({
      id: "unknown_report_kind",
      title: "材料类型识别门禁（unknown 比例）",
      status: "no_sample",
      detail: "尚无任务产物，无样本可判定。",
    });
  } else if (unknownRatio > UNKNOWN_REPORT_KIND_MAX_RATIO) {
    gates.push({
      id: "unknown_report_kind",
      title: "材料类型识别门禁（unknown 比例）",
      status: "fail",
      detail: `${unknownCount} 个任务（${(unknownRatio * 100).toFixed(1)}%）材料类型无法识别，高于 ${Math.round(UNKNOWN_REPORT_KIND_MAX_RATIO * 100)}% 上限；这些任务已强制转人工复核。`,
    });
  } else {
    gates.push({
      id: "unknown_report_kind",
      title: "材料类型识别门禁（unknown 比例）",
      status: "pass",
      detail: `材料类型无法识别的比例 ${(unknownRatio * 100).toFixed(1)}%（${unknownCount} 个任务），≤ ${Math.round(UNKNOWN_REPORT_KIND_MAX_RATIO * 100)}% 上限。`,
    });
  }

  return gates;
}

export interface StructuralGateVerdict {
  status: "pass" | "conditional" | "fail";
  label: string;
}

/**
 * 门禁清单的整体结论。
 *
 * 「有条件通过」只覆盖结构性维度：任一门禁无样本时结论是 conditional
 * （并注明哪些无样本），绝不输出"全部通过"掩盖样本缺口；
 * 任一 fail 则整体未达标。结论文案必须携带"仅覆盖结构性维度"限定，
 * 措辞对齐 docs/CI_BUSINESS_GATE.md 第 0 节（全绿不等于业务质量达标）。
 */
export function deriveStructuralGateVerdict(gates: StructuralGate[]): StructuralGateVerdict {
  if (gates.some((gate) => gate.status === "fail")) {
    return {
      status: "fail",
      label: "结构性维度：未达标（存在未通过的门禁项）",
    };
  }
  const noSampleCount = gates.filter((gate) => gate.status === "no_sample").length;
  if (noSampleCount > 0) {
    return {
      status: "conditional",
      label: `结构性维度：有条件通过（${noSampleCount} 项无样本）——仅覆盖结构性指标，全绿不等于业务质量达标`,
    };
  }
  return {
    status: "pass",
    label: "结构性维度：通过——仅覆盖结构性指标，全绿不等于业务质量达标",
  };
}

// ---------------------------------------------------------------------------
// 本版本未启用的指标（页面底部如实声明，措辞刻意避开未度量概念的名称）
// ---------------------------------------------------------------------------

export interface DisabledMetricNote {
  id: string;
  title: string;
  reason: string;
  /** 可追溯的文档依据。 */
  reference: string;
}

/**
 * 未启用指标清单。理由见 docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节。
 *
 * 注意：说明文案刻意不出现未度量概念的名称（问题检出效果类指标从未被
 * 度量，页面连名称都不呈现，防止用户看到名字去找数字），
 * 通过 reference 字段引用文档章节保证可追溯。
 */
export const DISABLED_METRIC_NOTES: DisabledMetricNote[] = [
  {
    id: "scan_text_recognition",
    title: "扫描件文字识别指标",
    reason: "本轮仅实现扫描页检测（检出即转人工复核），未实现自动文字识别，因此不存在对应的识别成功率指标。",
    reference: "docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节第 1 条",
  },
  {
    id: "annotated_corpus_regression",
    title: "人工标注语料回归验证",
    reason: "本轮无人工标注语料，基于标准答案的检出效果度量从未执行，页面不呈现任何此类数字。",
    reference: "docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节第 2 条",
  },
  {
    id: "recognition_correctness",
    title: "识别正确性度量",
    reason: "系统仅能度量「是否识别到」（识别率/未识别比例），无法度量「识别结果是否正确」；后者需要标准答案，本轮未建设。",
    reference: "docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节",
  },
];
