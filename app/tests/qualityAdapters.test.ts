import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import {
  DISABLED_METRIC_NOTES,
  computeDoneCoverageStats,
  computeFailureStageBarRatios,
  computeFailureStageWidthStyle,
  computeProcessingSuccessRate,
  computeUnassociatedOrgRatio,
  deriveFailureStageDistribution,
  deriveStructuralGateVerdict,
  deriveStructuralGates,
  formatRatioPercentText,
  type QualityMetricsResponse,
} from "../app/components/quality/qualityAdapters";

// 本文件测试 Task 7 质量管理页的纯逻辑层。红线断言（对照任务书）：
// - "分母为 0 显示 — 而非 100%"（处理成功率/证据完整率/覆盖率）；
// - "无失败任务时失败分布显示空态，不得凭空生成分段"；
// - "阶段集合不含 OCR"；
// - "不得把识别率标成准确率"——未启用指标说明文案连这些词都不出现；
// - 结构性门禁只有可机器判定的四条，不存在基于人工标注的分支。

// --- computeProcessingSuccessRate：分母为 0/null 时必须返回 null ----------------

assert.equal(
  computeProcessingSuccessRate(null),
  null,
  "REGRESSION: 未拉到 metrics 时处理成功率必须是 null（显示 —），不得显示 0% 或 100%",
);
assert.equal(
  computeProcessingSuccessRate({}),
  null,
  "REGRESSION: metrics 缺 jobs.total 时处理成功率必须是 null",
);
assert.equal(
  computeProcessingSuccessRate({ jobs: { total: 0 }, quality: { error_jobs: { count: 0, ratio: 0 } } }),
  null,
  "REGRESSION: 0 个任务时'成功率'没有定义，必须是 null 而非 100%",
);

assert.equal(
  computeProcessingSuccessRate({ jobs: { total: 100 }, quality: { error_jobs: { count: 5, ratio: 0.05 } } }),
  0.95,
  "正例：100 个任务 5 个 error 终态 → 成功率 0.95",
);
assert.equal(
  computeProcessingSuccessRate({ jobs: { total: 7 }, quality: { error_jobs: { count: 0, ratio: 0 } } }),
  1,
  "正例：全部成功 → 1（真实的全部成功，不是空样本）",
);
assert.equal(
  computeProcessingSuccessRate({ jobs: { total: 50 }, quality: { error_jobs: { count: 50, ratio: 1 } } }),
  0,
  "正例：全部失败 → 0（真实的全败，显示 0.0% 而非 —）",
);

// --- formatRatioPercentText：null → —，真实 0 → 0.0% ---------------------------

assert.equal(formatRatioPercentText(null), "—");
assert.equal(formatRatioPercentText(undefined), "—");
assert.equal(formatRatioPercentText(0.976), "97.6%");
assert.equal(formatRatioPercentText(0), "0.0%", "REGRESSION: 真实比例 0 必须显示 0.0%，不能与未知混淆");
assert.equal(formatRatioPercentText(1), "100.0%");

// --- deriveFailureStageDistribution ---------------------------------------------

const failedJob = (phase: string | null): JobSummaryRecord => ({
  job_id: `job-${phase ?? "none"}`,
  status: "error",
  stage_failed_at:
    phase === null
      ? undefined
      : { phase, phase_label: phase, percent: null, raw_stage: "某阶段" },
});

assert.equal(
  deriveFailureStageDistribution(null),
  null,
  "REGRESSION: 未拉到任务数据时必须是 null（加载态），不能与'没有失败任务'混为一谈",
);
assert.deepEqual(deriveFailureStageDistribution([]), [], "空任务列表 = 确认没有失败任务，返回空数组");
assert.deepEqual(
  deriveFailureStageDistribution([{ job_id: "ok", status: "done" }]),
  [],
  "REGRESSION: 没有失败任务时必须返回空数组（空态），不得凭空生成分段",
);

// 反例：非失败任务即使残留 stage_failed_at（失败后重跑成功的历史痕迹）也不计入
assert.deepEqual(
  deriveFailureStageDistribution([
    {
      job_id: "recovered",
      status: "done",
      stage_failed_at: { phase: "pdf_parse", phase_label: "PDF 解析", percent: null, raw_stage: "构建文档对象" },
    },
  ]),
  [],
  "REGRESSION: 只统计当前失败的任务；成功任务残留的 stage_failed_at 不得计入失败分布",
);

const distribution = deriveFailureStageDistribution([
  failedJob("pdf_parse"),
  failedJob("pdf_parse"),
  failedJob("rule_ai_analysis"),
  failedJob("quality_gate"),
  failedJob(null), // 失败但没有归因 → 未归因
  { job_id: "j-ocr", status: "error", stage_failed_at: { phase: "ocr", phase_label: "OCR", percent: null, raw_stage: "x" } }, // 脏数据：OCR 阶段不存在，必须归入未归因而不是渲染出来
]);
assert.deepEqual(
  distribution?.map((bucket) => [bucket.phase, bucket.count]),
  [
    ["pdf_parse", 2],
    ["unattributed", 2],
    ["rule_ai_analysis", 1],
    ["quality_gate", 1],
  ],
  "失败分布按 count 降序（同 count 按阶段序）；缺失归因与未知 phase（含 OCR 脏数据）" +
    "统一进'未归因'，绝不出现 OCR 分段（阶段枚举本就没有 OCR）",
);
assert.ok(
  distribution?.every((bucket) => bucket.phase !== "ocr"),
  "REGRESSION: 失败阶段集合不得包含 OCR（本轮不存在该能力）",
);
assert.equal(
  distribution?.find((bucket) => bucket.phase === "unattributed")?.label,
  "未归因",
);

// 条形宽度：最大分段满宽，其余按比例
const barRatios = computeFailureStageBarRatios(distribution ?? []);
assert.equal(barRatios.get("pdf_parse"), 1);
assert.equal(barRatios.get("rule_ai_analysis"), 0.5);
assert.deepEqual(computeFailureStageWidthStyle(barRatios, "pdf_parse"), { width: "100%" });
assert.deepEqual(computeFailureStageWidthStyle(barRatios, "unknown-phase"), { width: "0%" });

// --- computeDoneCoverageStats ----------------------------------------------------

assert.equal(computeDoneCoverageStats(null), null, "REGRESSION: 未拉到数据时无样本（null）");
assert.equal(
  computeDoneCoverageStats([{ job_id: "legacy", status: "done" }]),
  null,
  "REGRESSION: done 任务全部无 page_coverage 字段（历史任务形态）时必须返回 null，" +
    "不能判成'全部达标'或'全部不达标'",
);
assert.deepEqual(
  computeDoneCoverageStats([
    { job_id: "a", status: "done", page_coverage: 1.0 },
    { job_id: "b", status: "done", page_coverage: 0.5 },
    { job_id: "c", status: "review_required", page_coverage: 0.3 }, // 非 done 不在判定范围
    { job_id: "d", status: "done", page_coverage: null }, // 无字段不计入样本
  ]),
  { sampleSize: 2, lowCoverageCount: 1 },
  "只统计 done 且带 page_coverage 的任务；低覆盖（<0.8）如实计数",
);
assert.deepEqual(
  computeDoneCoverageStats([{ job_id: "a", status: "completed", page_coverage: 0.9 }]),
  { sampleSize: 1, lowCoverageCount: 0 },
  "completed 归一口径同样计入 done 判定",
);

// --- computeUnassociatedOrgRatio --------------------------------------------------

assert.equal(computeUnassociatedOrgRatio(null), null);
assert.equal(computeUnassociatedOrgRatio([]), null, "空任务列表没有分母，必须 null");
assert.equal(
  computeUnassociatedOrgRatio([
    { job_id: "a", organization_id: "org-1" },
    { job_id: "b", organization_id: null },
    { job_id: "c" },
    { job_id: "d", organization_id: "" },
  ]),
  0.75,
);

// --- deriveStructuralGates：结构性门禁四条，只有可机器判定的项 --------------------

const allPassMetrics: QualityMetricsResponse = {
  jobs: { total: 30 },
  quality: {
    unknown_report_kind: { count: 3, ratio: 0.1 },
    evidence_completeness: { findings_total: 20, findings_complete: 20, completeness_rate: 1.0, jobs_without_field: 5 },
  },
  report_id: { collision_count: 0 },
};

const allPassGates = deriveStructuralGates({
  metrics: allPassMetrics,
  doneCoverageStats: { sampleSize: 8, lowCoverageCount: 0 },
});

assert.equal(allPassGates.length, 4, "结构性门禁固定四条（不含基于人工标注的项）");
assert.deepEqual(
  allPassGates.map((gate) => gate.id),
  ["report_id_uniqueness", "page_coverage", "evidence_completeness", "unknown_report_kind"],
  "REGRESSION: 门禁 id 固定为四个结构性条目——基于人工标注的检出效果门禁" +
    "（无法机器判定）从设计上就不存在于本函数",
);
assert.deepEqual(
  allPassGates.map((gate) => gate.status),
  ["pass", "pass", "pass", "pass"],
  "正例：全样本达标时四条全通过",
);
// 证据完整率说明必须如实标注样本缺口（5 个任务无留痕）
assert.ok(
  allPassGates.find((gate) => gate.id === "evidence_completeness")?.detail.includes("5 个任务无证据留痕"),
  "样本缺口必须写进判定依据，不得静默忽略",
);

// 反例：任一指标不达标 → 对应门禁 fail
const failingGates = deriveStructuralGates({
  metrics: {
    jobs: { total: 30 },
    quality: {
      unknown_report_kind: { count: 20, ratio: 0.67 },
      evidence_completeness: { findings_total: 10, findings_complete: 5, completeness_rate: 0.5 },
    },
    report_id: { collision_count: 2 },
  },
  doneCoverageStats: { sampleSize: 8, lowCoverageCount: 3 },
});
assert.deepEqual(
  failingGates.map((gate) => gate.status),
  ["fail", "fail", "fail", "fail"],
  "反例：冲突/低覆盖/低完整率/高 unknown 各自独立判 fail",
);

// 反例：无样本 → no_sample，绝不判成通过
const noSampleGates = deriveStructuralGates({ metrics: null, doneCoverageStats: null });
assert.deepEqual(
  noSampleGates.map((gate) => gate.status),
  ["no_sample", "no_sample", "no_sample", "no_sample"],
  "REGRESSION: 无任何样本时四条全部'无样本'——空仓库不能伪装成达标系统",
);

const emptyEvidenceMetrics: QualityMetricsResponse = {
  jobs: { total: 5 },
  quality: {
    unknown_report_kind: { count: 0, ratio: 0 },
    // 分母为 0：证据完整率必须是 null（后端红线），门禁侧对应 no_sample
    evidence_completeness: { findings_total: 0, findings_complete: 0, completeness_rate: null },
  },
  report_id: { collision_count: 0 },
};
const emptyEvidenceGates = deriveStructuralGates({
  metrics: emptyEvidenceMetrics,
  doneCoverageStats: { sampleSize: 2, lowCoverageCount: 0 },
});
assert.equal(
  emptyEvidenceGates.find((gate) => gate.id === "evidence_completeness")?.status,
  "no_sample",
  "REGRESSION: 证据完整率分母为 0 → 无样本，绝不能显示 100% 或判通过",
);

// --- deriveStructuralGateVerdict ---------------------------------------------------

assert.equal(deriveStructuralGateVerdict(allPassGates).status, "pass");
assert.ok(
  deriveStructuralGateVerdict(allPassGates).label.includes("仅覆盖结构性指标"),
  "结论必须携带'仅覆盖结构性指标'限定（对齐 CI_BUSINESS_GATE.md 口径）",
);
assert.equal(deriveStructuralGateVerdict(noSampleGates).status, "conditional");
assert.ok(deriveStructuralGateVerdict(noSampleGates).label.includes("4 项无样本"));
assert.equal(deriveStructuralGateVerdict(failingGates).status, "fail");

// --- DISABLED_METRIC_NOTES：未启用指标说明 ---------------------------------------

assert.equal(DISABLED_METRIC_NOTES.length, 3, "未启用指标说明固定三条：扫描件识别/标注语料回归/识别正确性");
const FORBIDDEN_TERMS = ["召回率", "精确率", "Golden Corpus", "OCR", "准确率"];
for (const note of DISABLED_METRIC_NOTES) {
  assert.ok(note.title && note.reason && note.reference, "每条说明必须有标题、理由与文档依据");
  const text = `${note.title}${note.reason}${note.reference}`;
  for (const term of FORBIDDEN_TERMS) {
    assert.ok(
      !text.includes(term),
      `REGRESSION: 未启用指标说明不得出现「${term}」——这些度量从未执行，页面连名称都不呈现（${note.id}）`,
    );
  }
}

console.log("qualityAdapters.test.ts: all assertions passed");
