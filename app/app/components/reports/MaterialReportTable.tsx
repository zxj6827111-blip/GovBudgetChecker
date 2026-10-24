"use client";

import Link from "next/link";
import type { Route } from "next";
import { Badge, Td, Th } from "@/components/ui";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";
import { materialHistoryHref, materialScopeLabel, reviewLabel, versionSource, type MaterialReport, type MaterialRun } from "./materialReportAdapters";

export function materialTime(value?: string | null) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { hour12: false }) : "时间未记录";
}

export function analysisLabel(run?: MaterialRun | null) {
  if (!run) return "已上传 · 待分析";
  if (["done", "completed", "review_required", "degraded"].includes(run.status)) return "分析已结束";
  if (["error", "failed", "cancelled"].includes(run.status)) return "分析失败";
  if (run.status === "uploaded") return "已上传 · 待分析";
  return "分析中";
}

/** 当前文件的原件下载地址；未入材料槽位的历史任务按任务原件校验和下载。 */
export function materialSource(item: MaterialReport): string | null {
  const version = item.current_version;
  return version ? versionSource(version) : item.job_id && /^[a-f0-9]{64}$/.test(item.original_checksum ?? "")
    ? `/api/files/${encodeURIComponent(item.job_id)}/source?checksum=${item.original_checksum}` : null;
}

/** 复核入口：已复核看结果，可复核进入复核，其余只查看材料。 */
export function materialReviewAction(item: MaterialReport): { jobId: string | null; label: string } {
  const jobId = item.current_version?.current_run?.job_id ?? item.job_id ?? null;
  const canReview = ["pending", "in_progress", "invalidated", "completed"].includes(item.review.status);
  return { jobId, label: item.review.status === "completed" ? "查看复核结果" : canReview ? "进入复核" : "查看材料" };
}

function MaterialRow({ item, returnTo }: { item: MaterialReport; returnTo: string }) {
  const version = item.current_version;
  const run = version?.current_run;
  const source = materialSource(item);
  const action = materialReviewAction(item);
  return <tr data-testid={`material-report-${item.slot_id ?? item.job_id}`}>
    <Td><p className="font-medium">{item.fiscal_year ?? "年度待确认"} 年{item.report_kind === "final" ? "决算" : item.report_kind === "budget" ? "预算" : "类型待确认"}</p><p className="mt-1 text-xs text-slate-500">{item.organization_name ?? "主体待确认"} · {item.slot_id ? materialScopeLabel(item.material_scope) : "未归属记录"}</p>
      {item.mapping_required ? <p className="mt-1 text-xs text-warning-700">材料归属或口径待确认</p> : null}
      {item.slot_id ? <Link className="mt-2 inline-block text-xs text-primary-700 underline" href={materialHistoryHref(item.slot_id, returnTo) as Route}>版本与复核记录</Link> : null}
    </Td>
    <Td>{version ? <><Badge tone="processing">文件版本 {version.version_number} · {version.version_number > 1 ? "新提交版本" : "当前版本"}</Badge><p className="mt-1 break-all text-xs text-slate-500">{version.filename ?? "文件名未记录"}</p><p className="mt-1 text-sm">{version.analysis_unavailable ? "当前分析不可用或无访问权限" : analysisLabel(run)}</p></> : <p className="text-sm text-slate-500">{item.filename ?? "当前文件未确认"}</p>}
      <p className="mt-1 text-xs text-slate-500">{item.version_count === null ? "文件版本待确认" : `${item.version_count} 个文件版本`} · {item.analysis_task_count} 个分析任务</p>
    </Td>
    <Td><Badge tone={item.review.status === "completed" ? "done" : "review"}>{reviewLabel(item.review)}</Badge>{item.review.reviewer ? <p className="mt-1 text-xs text-slate-500">复核人：{item.review.reviewer}<br />{materialTime(item.review.reviewed_at)}</p> : null}{item.previous_review ? <p className="mt-2 text-xs text-slate-500">历史版本或分析已有复核结论，当前结论需另行确认</p> : null}</Td>
    <Td><div className="flex flex-col items-start gap-2">{action.jobId ? <Link className="whitespace-nowrap text-sm text-primary-700 underline" href={reviewDetailPath(action.jobId, returnTo) as Route}>{action.label}</Link> : <span className="text-xs text-slate-500">暂不可复核</span>}{source ? <a className="text-xs text-slate-600 underline" href={source}>下载原件</a> : <span className="text-xs text-slate-500">原件不可用或无访问权限</span>}</div></Td>
  </tr>;
}

/** returnTo 为复核页“返回”回到的部门报告地址。 */
export function MaterialReportTable({ items, returnTo = "/reports" }: { items: MaterialReport[]; returnTo?: string }) {
  return <div className="overflow-x-auto rounded-card border border-border bg-white"><table className="w-full text-left" data-testid="material-report-table"><thead><tr><Th>材料</Th><Th>当前文件／分析</Th><Th>人工复核</Th><Th>操作</Th></tr></thead><tbody>{items.map(item => <MaterialRow key={item.item_id} item={item} returnTo={returnTo} />)}</tbody></table></div>;
}
