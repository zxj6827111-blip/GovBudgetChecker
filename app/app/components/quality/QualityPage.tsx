/**
 * QualityPage：Task 7 质量管理页（仅真实数据，管理员可见）。
 *
 * 对照 `04-quality-management.png` 的布局：指标卡行 / 失败阶段分布 /
 * 关键指标条 / 发布门禁清单 / 未启用指标说明。刻意偏离项（均有明确理由，
 * 详见各区块注释与交付说明）：
 * - 原型图 6 张指标卡中的「扫描件文字识别成功率」卡不渲染（OCR 能力
 *   本轮未实现，无数据源，画出来就是伪造）；
 * - 原型图「人工标注语料回归」折线图整块不渲染（无标注语料，从未度量，
 *   空坐标系会暗示"以后会有数据"）；
 * - 原型图失败阶段分布的环形图改为横向条形（不为此引入图表库）；
 * - 原型图关键质量指标五条中，"正确性"类指标（需要标准答案）不渲染，
 *   只保留"是否识别到"的统计（识别率），标签如实区分两个概念；
 * - 原型图发布门禁四条中的"问题检出效果"门禁不渲染（无法机器判定）。
 *
 * 权限：本页由路由层用 AdminOnlyGuard 包裹；/api/metrics 本身 require_admin，
 * 请求失败（如会话过期）时指标区显示降级提示，不崩页。
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Badge, Card, Metric, SectionTitle, Td, Th } from "@/components/ui";
import type { JobSummaryRecord } from "@/lib/uiAdapters";

import { aggregatePageCoverage, formatCoveragePercentText } from "../workspace/workbenchAdapters";
import {
  computeDoneCoverageStats,
  computeFailureStageBarRatios,
  computeFailureStageWidthStyle,
  computeProcessingSuccessRate,
  computeUnassociatedOrgRatio,
  deriveFailureStageDistribution,
  deriveStructuralGateVerdict,
  deriveStructuralGates,
  DISABLED_METRIC_NOTES,
  formatRatioPercentText,
  type QualityMetricsResponse,
  type StructuralGate,
} from "./qualityAdapters";

/** 失败阶段条形的语义色（对照 docs/UI_COLOR_TOKEN_MAPPING.md 环形图分段取色；
 *  neutral-chart-400 仅作条形填充，不承载文字）。 */
const FAILURE_STAGE_BAR_CLASSES: Record<string, string> = {
  upload: "bg-primary-600",
  pdf_parse: "bg-info-600",
  metadata_recognition: "bg-warning-700",
  rule_ai_analysis: "bg-danger-600",
  quality_gate: "bg-neutral-chart-400",
  unattributed: "bg-slate-300",
};

const GATE_STATUS_META: Record<
  StructuralGate["status"],
  { label: string; badgeClass: string }
> = {
  pass: { label: "通过", badgeClass: "bg-success-100 text-success-700" },
  fail: { label: "未达标", badgeClass: "bg-danger-100 text-danger-700" },
  no_sample: { label: "无样本", badgeClass: "bg-slate-100 text-slate-500" },
};

const GATE_VERDICT_CLASSES: Record<string, string> = {
  pass: "bg-success-100 text-success-700",
  conditional: "bg-warning-100 text-warning-800",
  fail: "bg-danger-100 text-danger-700",
};

export function QualityPage() {
  const [metrics, setMetrics] = useState<QualityMetricsResponse | null>(null);
  const [metricsFailed, setMetricsFailed] = useState(false);
  const [jobs, setJobs] = useState<JobSummaryRecord[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadMetrics() {
      try {
        const response = await fetch("/api/metrics", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setMetricsFailed(true);
          }
          return;
        }
        const payload = (await response.json()) as QualityMetricsResponse;
        if (!cancelled) {
          setMetrics(payload);
          setMetricsFailed(false);
        }
      } catch {
        if (!cancelled) {
          setMetricsFailed(true);
        }
      }
    }

    async function loadJobs() {
      try {
        const response = await fetch("/api/jobs", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as
          | JobSummaryRecord[]
          | { items?: JobSummaryRecord[] };
        if (!cancelled) {
          setJobs(Array.isArray(payload) ? payload : payload.items ?? []);
        }
      } catch {
        // 保持 jobs=null（未拉到数据的展示态），不猜测。
      }
    }

    void loadMetrics();
    void loadJobs();
    return () => {
      cancelled = true;
    };
  }, []);

  const processingSuccessRate = useMemo(() => computeProcessingSuccessRate(metrics), [metrics]);
  const coverageAggregate = useMemo(() => aggregatePageCoverage(jobs), [jobs]);
  const unknownRatio = metrics?.quality?.unknown_report_kind?.ratio ?? null;
  const unknownCount = metrics?.quality?.unknown_report_kind?.count ?? null;
  const evidenceRate = metrics?.quality?.evidence_completeness?.completeness_rate ?? null;
  const evidenceTotal = metrics?.quality?.evidence_completeness?.findings_total ?? null;
  const reviewRequiredCount = metrics?.quality?.review_required?.count ?? null;
  const unresolvedYearRatio = metrics?.quality?.unresolved_report_year?.ratio ?? null;
  const unassociatedOrgRatio = useMemo(() => computeUnassociatedOrgRatio(jobs), [jobs]);

  const failureBuckets = useMemo(() => deriveFailureStageDistribution(jobs), [jobs]);
  const failureBarWidths = useMemo(
    () => (failureBuckets ? computeFailureStageBarRatios(failureBuckets) : new Map<string, number>()),
    [failureBuckets],
  );

  const doneCoverageStats = useMemo(() => computeDoneCoverageStats(jobs), [jobs]);
  const gates = useMemo(
    () => deriveStructuralGates({ metrics, doneCoverageStats }),
    [metrics, doneCoverageStats],
  );
  const gateVerdict = useMemo(() => deriveStructuralGateVerdict(gates), [gates]);

  return (
    <div className="p-8" data-testid="gbc-quality-page">
      <SectionTitle
        title="质量管理"
        desc="用真实处理指标与结构性发布门禁判断系统是否可信（仅呈现有真实数据的指标）。"
      />

      {metricsFailed ? (
        <div
          className="mt-4 flex items-center gap-2 rounded-card border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800"
          data-testid="gbc-quality-metrics-unavailable"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          指标端点暂不可用（可能为会话过期或服务暂不可达），以下依赖指标的数值显示为&ldquo;—&rdquo;，请刷新或重新登录后重试。
        </div>
      ) : null}

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Metric
          label="处理成功率"
          value={formatRatioPercentText(processingSuccessRate)}
          desc="口径：1 − error 终态任务占比；分母为已扫描任务产物总数（累计口径，非时间窗口）"
          tone="primary"
          data-testid="gbc-quality-metric-success-rate"
        />
        <Metric
          label="页面覆盖率"
          corner="Gate 2"
          value={formatCoveragePercentText(coverageAggregate)}
          desc={
            coverageAggregate
              ? `样本 ${coverageAggregate.sampleSize} 个任务（另有 ${coverageAggregate.excludedCount} 个无覆盖率字段，未纳入聚合）`
              : "暂无可聚合的覆盖率数据"
          }
          tone="primary"
          data-testid="gbc-quality-metric-page-coverage"
        />
        <Metric
          label="材料类型未识别比例"
          corner="Gate 3"
          value={formatRatioPercentText(unknownRatio)}
          desc={
            unknownRatio !== null && unknownCount !== null
              ? `${unknownCount} 个任务类型无法识别，已强制转人工复核`
              : "类型无法识别的任务占比"
          }
          tone="warning"
          data-testid="gbc-quality-metric-unknown-ratio"
        />
        <Metric
          label="证据完整率"
          corner="Gate 4"
          value={formatRatioPercentText(evidenceRate)}
          desc={
            evidenceRate !== null && evidenceTotal !== null
              ? `${evidenceTotal} 条 finding 中证据完整的占比（缺证据条目已降级或标记）`
              : "正式问题证据完整的占比；无 finding 样本时显示未知"
          }
          tone="success"
          data-testid="gbc-quality-metric-evidence-rate"
        />
        <Metric
          label="待人工复核"
          value={
            typeof reviewRequiredCount === "number" ? String(reviewRequiredCount) : null
          }
          desc="当前转人工复核的任务数（累计）"
          tone="warning"
          data-testid="gbc-quality-metric-review-required"
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card
          title="失败阶段分布"
          desc="失败任务在流水线阶段的归因统计（阶段集合不含扫描件文字识别——该能力本轮不存在）"
          data-testid="gbc-quality-failure-stages-card"
        >
          {failureBuckets === null ? (
            <div
              className="rounded-card border border-dashed border-border bg-surface-100 p-6 text-center text-sm text-slate-500"
              data-testid="gbc-quality-failure-stages-loading"
            >
              正在加载任务数据…
            </div>
          ) : failureBuckets.length === 0 ? (
            <div
              className="rounded-card border border-dashed border-border bg-surface-100 p-6 text-center text-sm text-slate-500"
              data-testid="gbc-quality-failure-stages-empty"
            >
              当前没有失败任务，无失败阶段可统计。
            </div>
          ) : (
            <ul className="space-y-3" data-testid="gbc-quality-failure-stages-list">
              {failureBuckets.map((bucket) => (
                <li key={bucket.phase} className="flex items-center gap-3">
                  <span className="w-28 shrink-0 text-sm text-slate-600">{bucket.label}</span>
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-surface-100">
                    <div
                      className={`h-full rounded-full ${FAILURE_STAGE_BAR_CLASSES[bucket.phase] ?? "bg-slate-300"}`}
                      style={computeFailureStageWidthStyle(failureBarWidths, bucket.phase)}
                    />
                  </div>
                  <span className="w-16 shrink-0 text-right text-sm font-semibold text-slate-700">
                    {bucket.count} 个
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="识别覆盖率指标"
          desc="「是否识别到」的统计（识别率），不是「识别得对不对」的正确性度量——后者需要标准答案，本轮未建设"
          data-testid="gbc-quality-recognition-card"
        >
          <ul className="space-y-4" data-testid="gbc-quality-recognition-list">
            <li>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">年份未识别比例</span>
                <span className="font-semibold text-slate-700">
                  {formatRatioPercentText(unresolvedYearRatio)}
                </span>
              </div>
              <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-surface-100">
                <div
                  className="h-full rounded-full bg-info-600"
                  style={{ width: `${formatRatioPercentWidth(unresolvedYearRatio)}%` }}
                />
              </div>
              <p className="mt-1 text-caption text-slate-400">
                年度无法识别的任务占比；未识别年份如实为空，不兜底默认值
              </p>
            </li>
            <li>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">材料类型未识别比例</span>
                <span className="font-semibold text-slate-700">
                  {formatRatioPercentText(unknownRatio)}
                </span>
              </div>
              <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-surface-100">
                <div
                  className="h-full rounded-full bg-warning-700"
                  style={{ width: `${formatRatioPercentWidth(unknownRatio)}%` }}
                />
              </div>
              <p className="mt-1 text-caption text-slate-400">
                类型无法识别的任务只跑通用规则并转人工复核
              </p>
            </li>
            <li>
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">组织未关联比例</span>
                <span className="font-semibold text-slate-700">
                  {formatRatioPercentText(unassociatedOrgRatio)}
                </span>
              </div>
              <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-surface-100">
                <div
                  className="h-full rounded-full bg-primary-600"
                  style={{ width: `${formatRatioPercentWidth(unassociatedOrgRatio)}%` }}
                />
              </div>
              <p className="mt-1 text-caption text-slate-400">
                未关联到组织树的任务占比（任务列表口径）
              </p>
            </li>
          </ul>
        </Card>
      </div>

      <Card
        title="发布门禁（仅结构性维度）"
        desc="本门禁清单只覆盖可机器判定的结构性指标，全绿不等于业务质量达标（docs/CI_BUSINESS_GATE.md）"
        className="mt-8"
        data-testid="gbc-quality-gates-card"
      >
        <table className="w-full border-collapse text-left" data-testid="gbc-quality-gates-table">
          <thead>
            <tr>
              <Th>门禁项</Th>
              <Th>状态</Th>
              <Th>判定依据（含样本量）</Th>
            </tr>
          </thead>
          <tbody>
            {gates.map((gate) => {
              const meta = GATE_STATUS_META[gate.status];
              return (
                <tr
                  key={gate.id}
                  data-testid={`gbc-quality-gate-${gate.id}`}
                  className="bg-surface-100"
                >
                  <Td>
                    <span className="font-medium text-slate-800">{gate.title}</span>
                  </Td>
                  <Td>
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${meta.badgeClass}`}
                      data-testid={`gbc-quality-gate-status-${gate.id}`}
                    >
                      {meta.label}
                    </span>
                  </Td>
                  <Td>
                    <span className="text-xs leading-5 text-slate-500">{gate.detail}</span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div
          className="mt-4 flex items-center gap-2 rounded-card px-4 py-3 text-sm font-semibold"
          data-testid="gbc-quality-gate-verdict"
        >
          <span
            className={`inline-flex items-center rounded-full px-3 py-1 ${GATE_VERDICT_CLASSES[gateVerdict.status] ?? "bg-slate-100 text-slate-600"}`}
          >
            当前版本建议：{gateVerdict.label}
          </span>
        </div>
      </Card>

      <Card
        title="本版本未启用的质量指标"
        desc="以下能力未实现或从未度量，页面不呈现任何对应数字；依据见 docs/RELEASE_ACCEPTANCE_2026-08-27.md 第 6 节"
        className="mt-8"
        data-testid="gbc-quality-disabled-metrics-card"
      >
        <ul className="space-y-3">
          {DISABLED_METRIC_NOTES.map((note) => (
            <li
              key={note.id}
              className="rounded-card border border-dashed border-border bg-surface-100 px-4 py-3"
              data-testid={`gbc-quality-disabled-${note.id}`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium text-slate-700">{note.title}</span>
                <Badge tone="neutral">未启用</Badge>
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-500">{note.reason}</p>
              <p className="mt-1 text-caption text-slate-400">依据：{note.reference}</p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

/** 识别覆盖率条形宽度（百分比数字）；未知（null）时为 0 宽（数值本身显示"—"）。 */
function formatRatioPercentWidth(ratio: number | null | undefined): number {
  if (typeof ratio !== "number" || !Number.isFinite(ratio)) {
    return 0;
  }
  return Math.min(100, Math.max(0, ratio * 100));
}

export default QualityPage;
