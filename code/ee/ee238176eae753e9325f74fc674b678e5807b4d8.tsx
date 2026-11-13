"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState } from "react";
import { Badge, Td, Th } from "@/components/ui";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";
import { materialScopeLabel, reviewLabel, versionSource, type MaterialReport, type MaterialRun, type MaterialVersion } from "./materialReportAdapters";

function time(value?: string | null) {
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

function AnalysisRevisions({ jobId }: { jobId: string }) {
  const [items, setItems] = useState<{ revision_id: string; is_current: boolean; created_at?: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void fetch(`/api/jobs/${encodeURIComponent(jobId)}/analysis-versions`, { cache: "no-store", signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`); return response.json(); })
      .then(payload => { setItems(payload.items); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); });
    return () => controller.abort();
  }, [jobId, open]);
  return <div><button type="button" className="text-xs text-primary-700 underline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "收起分析代际" : "查看分析代际"}</button>
    {open ? <div className="mt-1 text-xs text-slate-600">{error ? <p role="alert">{error}</p> : items === null ? "加载中…" : items.length === 0 ? "未保存分析代际记录" : items.map(item => <p key={item.revision_id}>{item.is_current ? "当前分析" : "历史分析"} · {time(item.created_at)} · <Link className="text-primary-700 underline" href={`/task/${encodeURIComponent(jobId)}?analysis_revision=${encodeURIComponent(item.revision_id)}` as Route}>查看分析结果</Link></p>)}</div> : null}</div>;
}

function History({ item }: { item: MaterialReport }) {
  const [versions, setVersions] = useState<MaterialVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/api/materials/reports/${encodeURIComponent(item.slot_id!)}/history`, { cache: "no-store", signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`版本历史加载失败（HTTP ${response.status}）`); return response.json(); })
      .then(payload => { setVersions(payload.data.versions); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); });
    return () => controller.abort();
  }, [item.slot_id, item.updated_at, item.current_version?.current_run?.analysis_revision, retry]);
  return <div className="mx-4 mb-4 rounded-md bg-slate-50 p-4" data-testid={`material-history-${item.slot_id}`}>
    <h4 className="text-sm font-semibold">文件版本与分析历史</h4>
    {error ? <p role="alert" className="mt-2 text-sm text-danger-700">{error} <button type="button" className="underline" onClick={() => setRetry(retry + 1)}>重试</button></p> : null}
    {versions === null && !error ? <p className="text-sm text-slate-500">正在加载历史…</p> : null}
    {versions?.map(version => <section key={version.document_version_id} className="mt-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2"><strong className="text-sm">文件版本 {version.version_number}</strong><Badge tone={version.is_current ? "processing" : "neutral"}>{version.is_current ? "当前版本" : "历史版本"}</Badge><span className="text-xs text-slate-500">登记时间：{time(version.created_at)}</span></div>
      <p className="mt-1 break-all text-xs text-slate-500">{version.filename ?? "文件名未记录"} · 文件指纹 {version.file_hash?.slice(0, 12) ?? "未记录"}…</p>
      {versionSource(version) ? <a className="mt-1 inline-block text-xs text-primary-700 underline" href={versionSource(version)!}>下载此版本原件</a> : <p className="text-xs text-slate-500">原件不可用或无访问权限</p>}
      {(version.runs ?? []).length === 0 ? <p className="mt-2 text-sm text-slate-600">尚无可查看的分析任务</p> : null}
      {version.runs?.map(run => <div key={run.job_id} className="ml-1 mt-2 border-l-2 border-border pl-3 text-sm">
        <p>{analysisLabel(run)} · {time(run.completed_at ?? run.created_at)} <span className="text-xs text-slate-500">任务 {run.job_id.slice(0, 8)}…</span></p>
        <Link className="text-xs text-primary-700 underline" href={reviewDetailPath(run.job_id, "/reports") as Route}>查看材料与复核记录</Link>
        <AnalysisRevisions jobId={run.job_id} />
      </div>)}
      {version.reviews?.filter(review => review.completed_at).map(review => <p key={review.analysis_basis_token} className="mt-2 text-xs text-slate-600">{version.is_current && version.review.status === "completed" && version.current_run?.job_id === review.analysis_job_uuid && review.analysis_basis_token === `${version.current_run.job_id}:${version.current_run.analysis_revision ?? 0}` && review.status === "completed" ? "当前复核" : "历史复核结论"} · {time(review.completed_at)} · {review.completed_by ?? "复核人未记录"} · {typeof review.confirmed_count === "number" ? `确认问题 ${review.confirmed_count} 项` : "问题数未记录"}</p>)}
    </section>)}
  </div>;
}

function MaterialRow({ item }: { item: MaterialReport }) {
  const [open, setOpen] = useState(false);
  const version = item.current_version;
  const run = version?.current_run;
  const source = version ? versionSource(version) : item.job_id && /^[a-f0-9]{64}$/.test(item.original_checksum ?? "")
    ? `/api/files/${encodeURIComponent(item.job_id)}/source?checksum=${item.original_checksum}` : null;
  const jobId = run?.job_id ?? item.job_id;
  const canReview = ["pending", "in_progress", "invalidated", "completed"].includes(item.review.status);
  return <>
    <tr data-testid={`material-report-${item.slot_id ?? item.job_id}`}>
      <Td><p className="font-medium">{item.fiscal_year ?? "年度待确认"} 年{item.report_kind === "final" ? "决算" : item.report_kind === "budget" ? "预算" : "类型待确认"}</p><p className="mt-1 text-xs text-slate-500">{item.organization_name ?? "主体待确认"} · {item.slot_id ? materialScopeLabel(item.material_scope) : "未归属记录"}</p>
        {item.mapping_required ? <p className="mt-1 text-xs text-warning-700">材料归属或口径待确认</p> : null}
        {item.slot_id ? <button type="button" aria-expanded={open} className="mt-2 text-xs text-primary-700 underline" onClick={() => setOpen(!open)}>{open ? "收起版本与分析历史" : "展开版本与分析历史"}</button> : null}
      </Td>
      <Td>{version ? <><Badge tone="processing">文件版本 {version.version_number} · {version.version_number > 1 ? "新提交版本" : "当前版本"}</Badge><p className="mt-1 break-all text-xs text-slate-500">{version.filename ?? "文件名未记录"}</p><p className="mt-1 text-sm">{version.analysis_unavailable ? "当前分析不可用或无访问权限" : analysisLabel(run)}</p></> : <p className="text-sm text-slate-500">{item.filename ?? "当前文件未确认"}</p>}
        <p className="mt-1 text-xs text-slate-500">{item.version_count === null ? "文件版本待确认" : `${item.version_count} 个文件版本`} · {item.analysis_task_count} 个分析任务</p>
      </Td>
      <Td><Badge tone={item.review.status === "completed" ? "done" : "review"}>{reviewLabel(item.review)}</Badge>{item.review.reviewer ? <p className="mt-1 text-xs text-slate-500">复核人：{item.review.reviewer}<br />{time(item.review.reviewed_at)}</p> : null}{item.previous_review ? <p className="mt-2 text-xs text-slate-500">历史版本或分析已有复核结论，当前结论需另行确认</p> : null}</Td>
      <Td><div className="flex flex-col items-start gap-2">{jobId ? <Link className="whitespace-nowrap text-sm text-primary-700 underline" href={reviewDetailPath(jobId, "/reports") as Route}>{item.review.status === "completed" ? "查看复核结果" : canReview ? "进入复核" : "查看材料"}</Link> : <span className="text-xs text-slate-500">暂不可复核</span>}{source ? <a className="text-xs text-slate-600 underline" href={source}>下载原件</a> : <span className="text-xs text-slate-500">原件不可用或无访问权限</span>}</div></Td>
    </tr>
    {open && item.slot_id ? <tr><td colSpan={4}><History item={item} /></td></tr> : null}
  </>;
}

export function MaterialReportTable({ items }: { items: MaterialReport[] }) {
  return <div className="overflow-x-auto rounded-card border border-border bg-white"><table className="w-full text-left" data-testid="material-report-table"><thead><tr><Th>材料</Th><Th>当前文件／分析</Th><Th>人工复核</Th><Th>操作</Th></tr></thead><tbody>{items.map(item => <MaterialRow key={item.item_id} item={item} />)}</tbody></table></div>;
}
