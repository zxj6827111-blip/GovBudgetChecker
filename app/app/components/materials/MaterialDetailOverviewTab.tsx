"use client";

import { Card, Metric } from "@/components/ui";

import {
  COVERAGE_UNAVAILABLE_LABEL,
  presentAnalysisAvailability,
  type SlotDetailData,
  type SlotStateNotice,
} from "./materialDetailAdapters";
import { formatCount } from "../../../lib/materialDetailPresentation";
import { MaterialStatusBadge } from "./MaterialStatusBadge";
import {
  formatMaterialTimestamp,
  presentApplicability,
  presentCaliber,
  presentMaterialScope,
  presentReportKind,
  presentSubjectKind,
} from "@/lib/materialStatusPresentation";

/**
 * MaterialDetailOverviewTab（页面 10 / 13）：材料概览。
 *
 * 页面 13 的"缺失态"不是另一个页面，而是本 Tab 在
 * `slot.status === 'missing'` 时的形态 —— 用同一个组件渲染，口径就不会分叉。
 *
 * 三条关键口径
 * ------------
 * 1. **只有真实 `status='missing'` 才进缺失态**（§五十七）；
 * 2. **缺失态下若该槽位存在历史版本，只能写"当前无有效文件版本"**，
 *    不能写"从未上传"（§五十八）—— 判断在 `presentSlotState` 一处完成；
 * 3. **`formal_issue_count` 为 null 显示"—"**，为 0 显示 `0`：前者是
 *    "还没算出可确认结论"，后者是"已确认没有正式 finding"（§五十三）。
 *    该字段的口径与审核工作台/质量门禁一致（`count_formal_findings`，
 *    info 计入），因此界面把它叫「正式检查记录」而不是「正式问题」——
 *    「正式问题」是检查结果页里"严重度为 error/warn"那一栏的名字，
 *    两者同名会让用户以为总计漏算了「信息提示」。
 */
export interface MaterialDetailOverviewTabProps {
  data: SlotDetailData;
  state: SlotStateNotice;
}

export function MaterialDetailOverviewTab({ data, state }: MaterialDetailOverviewTabProps) {
  const slot = data.slot;
  const analysis = data.current_analysis;
  const coverage = analysis?.coverage ?? null;

  const metaRows: Array<{ label: string; value: string; testId: string }> = [
    {
      label: "主体类型",
      value: `${presentSubjectKind(slot.subject_kind)} · ${presentMaterialScope(slot.material_scope)}`,
      testId: "gbc-material-overview-subject-kind",
    },
    {
      label: "口径",
      value: presentCaliber(slot.caliber),
      testId: "gbc-material-overview-caliber",
    },
    {
      label: "文种",
      value: presentReportKind(slot.report_kind),
      testId: "gbc-material-overview-report-kind",
    },
    {
      label: "适用性",
      value: presentApplicability(slot.applicability_status),
      testId: "gbc-material-overview-applicability",
    },
    {
      label: "适用性说明",
      value: slot.applicability_note ?? "—",
      testId: "gbc-material-overview-applicability-note",
    },
    {
      label: "截止时间",
      value: slot.due_at ? formatMaterialTimestamp(slot.due_at) : "未登记（无法判断是否逾期）",
      testId: "gbc-material-overview-due-at",
    },
    {
      label: "当前文件版本",
      value: data.current_version
        ? `v${data.current_version.document_version_id}（${data.current_version.original_filename ?? "文件名未记录"}）`
        : "无当前文件版本",
      testId: "gbc-material-overview-current-version",
    },
    {
      label: "历史版本数",
      value: formatCount(data.historical_version_count),
      testId: "gbc-material-overview-history-count",
    },
    {
      label: "最后更新时间",
      value: formatMaterialTimestamp(slot.updated_at),
      testId: "gbc-material-overview-updated-at",
    },
  ];

  return (
    <div className="space-y-5">
      <Card title="材料状态" desc="状态与成因成对出现，避免只给一个颜色让人猜">
        <div className="flex flex-wrap items-center gap-3">
          <MaterialStatusBadge status={slot.status} testId="gbc-material-overview-status" />
          <span className="text-sm text-slate-600" data-testid="gbc-material-overview-reason">
            {slot.status_reason ?? "未记录成因"}
          </span>
        </div>
        {state.detail ? (
          <p
            className="mt-3 rounded-md border border-border bg-surface-100 px-3 py-2 text-xs text-slate-600"
            data-testid="gbc-material-overview-state-detail"
          >
            {state.detail}
          </p>
        ) : null}
        {state.isMissing ? (
          <p
            className="mt-2 text-xs text-danger-700"
            data-testid="gbc-material-overview-missing-note"
          >
            「逾期未上传」的含义是「已过截止时间且当前没有文件」，不代表这份材料
            历史上没有文件：如果该槽位下有历史版本，页面只会写「当前无有效文件版本」。
            本轮也没有提供自动绑定入口，上传请走上传中心。
          </p>
        ) : null}
      </Card>

      <Card title="材料属性" desc="身份、口径与截止时间">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
          {metaRows.map((row) => (
            <div key={row.label} className="flex justify-between gap-4 border-b border-border pb-2">
              <dt className="text-sm text-slate-500">{row.label}</dt>
              <dd className="text-right text-sm text-slate-800" data-testid={row.testId}>
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Metric
          label="正式检查记录"
          value={formatCount(analysis?.formal_issue_count ?? null)}
          desc={
            analysis?.available
              ? "当前文件版本上已确认的正式 finding（含信息提示；降级待人工核验项不计入）"
              : "当前文件版本暂无可确认的分析结果，因此这个数字算不出来"
          }
          tone="danger"
          data-testid="gbc-material-overview-formal-count"
        />
        <Metric
          label="需人工核验"
          value={formatCount(analysis?.manual_review_items?.length ?? null)}
          desc="证据不足被降级的候选问题，不是已确认的问题"
          tone="warning"
          data-testid="gbc-material-overview-manual-count"
        />
        <Metric
          label="检查义务完成"
          value={
            coverage?.available && coverage.summary
              ? `${coverage.summary.completed_total} / ${coverage.summary.applicable_total}`
              : null
          }
          desc={
            coverage?.available
              ? "当前文件版本的检查覆盖"
              : COVERAGE_UNAVAILABLE_LABEL
          }
          tone="info"
          data-testid="gbc-material-overview-coverage-count"
        />
      </div>

      <Card title="当前分析" desc="只对应当前文件版本；历史版本的分析不会在这里出现">
        {analysis?.available ? (
          <div className="space-y-2 text-sm text-slate-700">
            <p data-testid="gbc-material-overview-analysis-available">
              分析运行：{analysis.run?.job_uuid ?? "未记录"}（
              {analysis.run?.completed_at ? formatMaterialTimestamp(analysis.run.completed_at) : "完成时间未记录"}）
            </p>
            <p className="text-xs text-slate-500">
              正式检查记录 {formatCount(analysis.formal_issue_count)} 条 · 需人工核验{" "}
              {formatCount(analysis.manual_review_items?.length ?? null)} 条 · 信息提示{" "}
              {formatCount(analysis.info_findings?.length ?? null)} 条
            </p>
          </div>
        ) : (
          <p
            className="text-sm text-slate-600"
            data-testid="gbc-material-overview-analysis-unavailable"
          >
            {presentAnalysisAvailability(analysis?.reason)}
          </p>
        )}
      </Card>
    </div>
  );
}

export default MaterialDetailOverviewTab;
