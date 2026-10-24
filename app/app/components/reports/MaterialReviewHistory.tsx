"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui";
import { reviewDetailPath } from "../review-workbench/reviewListAdapters";
import { analysisLabel, materialTime } from "./MaterialReportTable";
import { versionSource, type MaterialVersion } from "./materialReportAdapters";

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
    {open ? <div className="mt-1 text-xs text-slate-600">{error ? <p role="alert">{error}</p> : items === null ? "加载中…" : items.length === 0 ? "未保存分析代际记录" : items.map(item => <p key={item.revision_id}>{item.is_current ? "当前分析" : "历史分析"} · {materialTime(item.created_at)} · <Link className="text-primary-700 underline" href={`/task/${encodeURIComponent(jobId)}?analysis_revision=${encodeURIComponent(item.revision_id)}` as Route}>查看分析结果</Link></p>)}</div> : null}</div>;
}

/**
 * 一份材料的文件版本、分析任务与复核结论，按版本从新到旧列出。
 * 从部门报告移到材料详情“版本与来源”；returnTo 为复核页“返回”回到的地址。
 */
export function MaterialReviewHistory({ slotId, returnTo }: { slotId: string; returnTo: string }) {
  const [versions, setVersions] = useState<MaterialVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/api/materials/reports/${encodeURIComponent(slotId)}/history`, { cache: "no-store", signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`版本历史加载失败（HTTP ${response.status}）`); return response.json(); })
      .then(payload => {
        if (!Array.isArray(payload?.data?.versions)) throw new Error("版本历史响应无效");
        setVersions(payload.data.versions); setError(null);
      })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); });
    return () => controller.abort();
  }, [slotId, retry]);
  return <div data-testid={`material-history-${slotId}`}>
    {error ? <p role="alert" className="text-sm text-danger-700">{error} <button type="button" className="underline" onClick={() => setRetry(retry + 1)}>重试</button></p> : null}
    {versions === null && !error ? <p className="text-sm text-slate-500">正在加载历史…</p> : null}
    {versions?.length === 0 ? <p className="text-sm text-slate-500">尚无可查看的文件版本或分析记录。</p> : null}
    {versions?.map((version, index) => <section key={version.document_version_id} className={index ? "mt-3 border-t border-border pt-3" : ""}>
      <div className="flex flex-wrap items-center gap-2"><strong className="text-sm">文件版本 {version.version_number}</strong><Badge tone={version.is_current ? "processing" : "neutral"}>{version.is_current ? "当前版本" : "历史版本"}</Badge><span className="text-xs text-slate-500">登记时间：{materialTime(version.created_at)}</span></div>
      <p className="mt-1 break-all text-xs text-slate-500">{version.filename ?? "文件名未记录"} · 文件指纹 {version.file_hash?.slice(0, 12) ?? "未记录"}…</p>
      {versionSource(version) ? <a className="mt-1 inline-block text-xs text-primary-700 underline" href={versionSource(version)!}>下载此版本原件</a> : <p className="text-xs text-slate-500">原件不可用或无访问权限</p>}
      {(version.runs ?? []).length === 0 ? <p className="mt-2 text-sm text-slate-600">尚无可查看的分析任务</p> : null}
      {version.runs?.map(run => <div key={run.job_id} className="ml-1 mt-2 border-l-2 border-border pl-3 text-sm">
        <p>{analysisLabel(run)} · {materialTime(run.completed_at ?? run.created_at)} <span className="text-xs text-slate-500">任务 {run.job_id.slice(0, 8)}…</span></p>
        <Link className="text-xs text-primary-700 underline" href={reviewDetailPath(run.job_id, returnTo) as Route}>查看材料与复核记录</Link>
        <AnalysisRevisions jobId={run.job_id} />
      </div>)}
      {version.reviews?.filter(review => review.completed_at).map(review => <p key={review.analysis_basis_token} className="mt-2 text-xs text-slate-600">{version.is_current && version.review.status === "completed" && version.current_run?.job_id === review.analysis_job_uuid && review.analysis_basis_token === `${version.current_run.job_id}:${version.current_run.analysis_revision ?? 0}` && review.status === "completed" ? "当前复核" : "历史复核结论"} · {materialTime(review.completed_at)} · {review.completed_by ?? "复核人未记录"} · {typeof review.confirmed_count === "number" ? `确认问题 ${review.confirmed_count} 项` : "问题数未记录"}</p>)}
    </section>)}
  </div>;
}
