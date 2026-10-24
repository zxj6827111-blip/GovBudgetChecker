"use client";

import Link from "next/link";
import type { Route } from "next";
import { Badge } from "@/components/ui";
import { BUTTON_BASE_CLASSES, BUTTON_SIZE_CLASSES, BUTTON_VARIANT_CLASSES } from "@/components/ui/buttonStyles";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";
import { analysisLabel, materialReviewAction, materialSource, materialTime } from "./MaterialReportTable";
import { materialHistoryHref, reviewLabel, type MaterialReport } from "./materialReportAdapters";

const KIND_LABELS: Record<string, string> = { budget: "预算", final: "决算" };

function yearLabel(year: string | number | null | undefined) {
  return year && year !== "unknown" ? `${year} 年` : "年度待确认 · ";
}

/**
 * 某个财政年度的一份预算或决算：复核状态、当前文件与分析、复核人和入口。
 * 文件版本、分析代际与历史复核结论在材料详情“版本与来源”中查看。
 */
export function MaterialReportCard({ item, returnTo, subjectId }: { item: MaterialReport; returnTo: string; subjectId: string }) {
  const version = item.current_version;
  const action = materialReviewAction(item);
  const source = materialSource(item);
  return <article className="flex min-w-0 flex-col rounded-card border border-border bg-white p-4" data-testid={`material-report-${item.slot_id ?? item.job_id}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <h3 className="text-base font-semibold text-slate-900">{yearLabel(item.fiscal_year)}{KIND_LABELS[item.report_kind] ?? "类型待确认"}</h3>
        {/* 部门汇总偶有下属单位发布；主体不是当前组织时写明是谁。 */}
        {item.organization_id !== subjectId ? <p className="mt-0.5 text-xs text-slate-500">{item.organization_name ?? "主体待确认"}</p> : null}
      </div>
      <Badge tone={item.review.status === "completed" ? "done" : item.review.status === "history_only" ? "neutral" : "review"}>{reviewLabel(item.review)}</Badge>
    </div>
    <p className="mt-3 break-all text-sm text-slate-700">{version?.filename ?? item.filename ?? "文件名未记录"}</p>
    <p className="mt-1 text-xs text-slate-500">{version
      ? `文件版本 ${version.version_number}${(item.version_count ?? 1) > 1 ? `（共 ${item.version_count} 个版本）` : ""} · ${version.analysis_unavailable ? "当前分析不可用或无访问权限" : analysisLabel(version.current_run)}`
      : item.slot_id ? "当前文件未确认" : "未归属记录 · 文件版本待确认"}</p>
    {item.review.reviewer ? <p className="mt-1 text-xs text-slate-500">复核人 {item.review.reviewer} · {materialTime(item.review.reviewed_at)}</p> : null}
    {item.previous_review ? <p className="mt-1 text-xs text-slate-500">历史版本或分析已有复核结论，当前结论需另行确认</p> : null}
    {item.mapping_required ? <p className="mt-1 text-xs text-warning-700">材料归属或口径待确认</p> : null}
    <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-2 pt-4 text-sm">
      {action.jobId ? <Link href={reviewDetailPath(action.jobId, returnTo) as Route} className={`${BUTTON_BASE_CLASSES} ${BUTTON_SIZE_CLASSES.sm} ${BUTTON_VARIANT_CLASSES.primary}`}>{action.label}</Link> : <span className="text-xs text-slate-500">暂不可复核</span>}
      {source ? <a href={source} className="text-slate-600 underline">下载原件</a> : <span className="text-xs text-slate-500">原件不可用或无访问权限</span>}
      {item.slot_id ? <Link href={materialHistoryHref(item.slot_id, returnTo) as Route} className="text-primary-700 underline">版本与复核记录</Link> : null}
    </div>
  </article>;
}

/** 该年度没有这一类材料：只写暂无，不推断应交未交。 */
export function MaterialReportCardEmpty({ kind, year }: { kind: "budget" | "final"; year: string }) {
  return <div className="rounded-card border border-dashed border-border bg-white p-4" data-testid={`material-report-empty-${kind}`}>
    <h3 className="text-base font-semibold text-slate-500">{yearLabel(year)}{KIND_LABELS[kind]}</h3>
    <p className="mt-2 text-sm text-slate-500">暂无材料。</p>
  </div>;
}
