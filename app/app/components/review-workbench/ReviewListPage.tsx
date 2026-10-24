"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, SectionTitle } from "@/components/ui";
import { publishReviewPendingCount, REVIEW_WORKLIST_INVALIDATED } from "@/lib/reviewWorklistEvents";
import { reviewWorklistRequest } from "@/lib/listRequests";
import { formatReviewMoment } from "./reviewLifecycleAdapters";
import {
  filterReviewList, reviewDetailPath, reviewListFilters, reviewListPath, REVIEW_LIST_LABELS,
  type ReviewListData, type ReviewListFilters, type ReviewListItem, type ReviewListFilter,
} from "./reviewListAdapters";

const PAGE_SIZE = 20;

export function ReviewListPage() {
  const params = useSearchParams();
  const router = useRouter();
  const paramsKey = params.toString();
  const [filters, setFilters] = useState(() => reviewListFilters(params));
  const filtersRef = useRef(filters);
  const requestedPath = useRef<string | null>(null);
  const requestedPaths = useRef(new Set<string>());
  useEffect(() => {
    const next = reviewListFilters(new URLSearchParams(paramsKey));
    const path = reviewListPath(next);
    // 连续操作立即更新本地筛选；较早的路由响应不能覆盖用户刚输入的条件。
    if (requestedPath.current && path !== requestedPath.current && requestedPaths.current.has(path)) return;
    requestedPath.current = null; requestedPaths.current.clear();
    filtersRef.current = next; setFilters(next);
  }, [paramsKey]);
  const listPath = reviewListPath(filters);
  const [data, setData] = useState<ReviewListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const restoredPath = useRef<string | null>(null);
  const requestSeq = useRef(0);
  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const nextData = await reviewWorklistRequest.load();
      if (seq !== requestSeq.current) return;
      setData(nextData); setError(null); publishReviewPendingCount(nextData.pending_total);
    } catch (reason) {
      if (seq !== requestSeq.current) return;
      setError(reason instanceof Error && reason.name === "AbortError" ? "复核清单读取超时，请点击刷新重试。" : reason instanceof Error ? reason.message : "复核清单读取失败。"); publishReviewPendingCount(null);
    } finally { if (seq === requestSeq.current) setLoading(false); }
  }, []);
  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener(REVIEW_WORKLIST_INVALIDATED, refresh);
    window.addEventListener("focus", refresh);
    const timer = window.setInterval(refresh, 30_000);
    return () => { requestSeq.current += 1; window.clearInterval(timer); window.removeEventListener("focus", refresh); window.removeEventListener(REVIEW_WORKLIST_INVALIDATED, refresh); };
  }, [load]);
  useEffect(() => {
    if (!data || restoredPath.current === listPath) return;
    restoredPath.current = listPath;
    const main = root.current?.closest("main");
    try { if (main) main.scrollTop = Number(sessionStorage.getItem(`gbc:review-scroll:${listPath}`)) || 0; } catch { /* 存储不可用不影响导航。 */ }
  }, [data, listPath]);
  const update = (patch: Partial<ReviewListFilters>) => {
    const next = { ...filtersRef.current, ...patch, page: patch.page ?? 1 };
    filtersRef.current = next; setFilters(next);
    const path = reviewListPath(next);
    requestedPath.current = path; requestedPaths.current.add(path);
    router.replace(path as Route, { scroll: false });
  };
  const rememberScroll = () => {
    try { sessionStorage.setItem(`gbc:review-scroll:${listPath}`, String(root.current?.closest("main")?.scrollTop ?? 0)); } catch { /* URL 已保留筛选，滚动恢复只是辅助。 */ }
  };
  const items = data?.items ?? [];
  const filtered = filterReviewList(items, filters);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const page = Math.min(filters.page, pageCount);
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const years = Array.from(new Set(items.map(item => item.fiscal_year).filter((year): year is number => year !== null))).sort((a, b) => b - a);
  const organizations = new Map(items.filter(item => item.organization_id).map(item => [item.organization_id!, item.organization_name || "名称未识别到"]));
  const lastReview = items.filter(item => item.review_status === "in_progress" && item.reviewer === data?.viewer)
    .sort((a, b) => (Date.parse(b.reviewed_at ?? "") || 0) - (Date.parse(a.reviewed_at ?? "") || 0))[0];
  const openLink = (item: ReviewListItem, label?: string) => item.job_id ? (
    <Link href={reviewDetailPath(item.job_id, listPath) as Route} onClick={rememberScroll}
      data-testid={`gbc-review-list-open-${item.job_id}`}
      className="inline-flex whitespace-nowrap rounded-md border border-border bg-white px-3 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">
      {label ?? (item.review_status === "completed" ? "查看结论" : item.review_status === "in_progress" ? "继续复核" : item.review_status === "history_only" ? "查看历史结果" : "查看材料")}
    </Link>
  ) : <span className="text-xs text-slate-400">暂无分析结果</span>;

  return <div ref={root} className="p-4 md:p-8" data-testid="gbc-review-list-page">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1"><SectionTitle title="人工复核" desc="先选择材料，再核对原文与问题。查看材料不会自动开始复核。" /></div>
      <Button size="sm" variant="secondary" onClick={() => void load()} disabled={loading}>{loading ? "正在刷新…" : "刷新清单"}</Button>
    </div>
    {/* 无数据时重新读取期间只显示"正在读取"，不让上一次的失败提示与读取中同时出现。 */}
    {error && !(loading && !data) ? <div role="alert" className="mt-4 rounded-md border border-warning-200 bg-warning-50 p-3 text-sm text-warning-800" data-testid="gbc-review-list-error">{error}{data ? " 当前展示的是上次成功读取的数据。" : ""}</div> : null}
    {lastReview ? <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-white p-4" data-testid="gbc-review-list-continue">
      <div><div className="text-xs text-slate-500">上次开始的复核</div><div className="mt-1 text-sm text-slate-800">{lastReview.organization_name ?? "主体未识别到"} · {lastReview.fiscal_year ?? "年度未识别到"} 年{lastReview.report_kind === "budget" ? "预算" : lastReview.report_kind === "final" ? "决算" : "文种未识别到"}</div></div>
      {openLink(lastReview, "继续上次复核")}
    </div> : null}
    <div className="mt-5 flex flex-wrap gap-2" aria-label="按复核状态筛选">
      {(Object.keys(REVIEW_LIST_LABELS) as ReviewListFilter[]).map(status => <button key={status} type="button"
        onClick={() => update({ status })} aria-pressed={filters.status === status} data-testid={`gbc-review-list-status-${status}`}
        className={`rounded-md border px-3 py-2 text-sm ${filters.status === status ? "border-primary-700 bg-primary-700 text-white" : "border-border bg-white text-slate-600 hover:bg-surface-100"}`}>
        {REVIEW_LIST_LABELS[status]} {data ? status === "todo" ? data.pending_total : status === "all" ? items.length : data.counts[status] ?? "—" : "—"}
      </button>)}
    </div>
    <div className="mt-4 flex flex-wrap gap-2">
      <input value={filters.keyword} onChange={event => update({ keyword: event.target.value })} placeholder="搜索文件名、部门或单位" aria-label="搜索复核材料" data-testid="gbc-review-list-search" className="min-w-[180px] flex-1 rounded-md border border-border bg-white px-3 py-2 text-sm" />
      <select value={filters.year} onChange={event => update({ year: event.target.value })} aria-label="按财政年度筛选" data-testid="gbc-review-list-year" className="rounded-md border border-border bg-white px-3 py-2 text-sm"><option value="">全部年度</option>{years.map(year => <option key={year} value={year}>{year} 年</option>)}<option value="unknown">年度未识别到</option></select>
      <select value={filters.organization} onChange={event => update({ organization: event.target.value })} aria-label="按部门或单位筛选" className="max-w-full rounded-md border border-border bg-white px-3 py-2 text-sm"><option value="">全部部门 / 单位</option>{Array.from(organizations).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
      <select value={filters.kind} onChange={event => update({ kind: event.target.value })} aria-label="按文种筛选" className="rounded-md border border-border bg-white px-3 py-2 text-sm"><option value="all">全部文种</option><option value="budget">预算</option><option value="final">决算</option><option value="unknown">文种未识别到</option></select>
    </div>
    {loading && !data ? <div className="py-16 text-center text-sm text-slate-500">正在读取复核清单…</div> : data ? <>
      <div className="mt-4 overflow-x-auto rounded-lg border border-border bg-white">
        <table className="w-full text-left text-sm" data-testid="gbc-review-list-table"><thead className="border-b border-border bg-surface-50 text-xs text-slate-500"><tr>{["材料", "复核状态", "待处理事项", "复核人 / 时间", "操作"].map(title => <th key={title} className={`px-4 py-3 font-medium ${title === "复核人 / 时间" ? "hidden lg:table-cell" : ""}`}>{title}</th>)}</tr></thead><tbody>
          {visible.map(item => <tr key={item.item_id} className="border-b border-border last:border-0" data-testid={`gbc-review-list-row-${item.job_id}`}>
            <td className="min-w-[180px] px-3 py-4 md:min-w-[220px] md:px-4"><div className="font-medium text-slate-800">{item.organization_name ?? "主体未识别到"}</div><div className="mt-1 text-xs text-slate-500">{item.fiscal_year ?? "年度未识别到"} 年{item.report_kind === "budget" ? "预算" : item.report_kind === "final" ? "决算" : "文种未识别到"}{item.document_version_id !== null ? " · 当前版本" : ""}</div><div className="mt-1 max-w-lg break-words text-xs text-slate-400">{item.filename ?? "文件名未识别到"}</div></td>
            <td className="whitespace-nowrap px-4 py-4"><Badge tone={item.review_status === "completed" ? "done" : item.review_status === "in_progress" ? "processing" : item.review_status === "history_only" || item.review_status === "waiting_analysis" ? "neutral" : "review"}>{REVIEW_LIST_LABELS[item.review_status]}</Badge></td>
            <td className="whitespace-nowrap px-4 py-4 text-xs text-slate-600">{item.review_status === "history_only" ? "缺少准确材料关联" : item.review_status === "waiting_analysis" ? "请在处理队列查看进度" : <><div>{item.pending_findings ?? "—"} 条问题待判定</div><div className="mt-1">{item.pending_checks === null ? "检查覆盖未知" : `${item.pending_checks} 项检查待补核`}</div></>}</td>
            <td className="hidden whitespace-nowrap px-4 py-4 text-xs text-slate-500 lg:table-cell"><div>{item.reviewer ?? "尚未开始"}</div><div className="mt-1">{formatReviewMoment(item.reviewed_at) ?? "—"}</div></td>
            <td className="px-4 py-4">{item.review_status === "waiting_analysis" && item.job_id ? <Link className="whitespace-nowrap text-xs text-primary-700" href={`/queue?job=${encodeURIComponent(item.job_id)}` as Route}>查看处理进度</Link> : openLink(item)}</td>
          </tr>)}
          {visible.length === 0 ? <tr><td colSpan={5} className="py-14 text-center text-sm text-slate-500">{filters.status === "todo" && !filters.keyword && !filters.year && !filters.organization && filters.kind === "all" ? "当前没有待办材料，可切换到已完成或历史结果查看。" : "没有符合当前筛选条件的材料。"}</td></tr> : null}
        </tbody></table>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>共 {filtered.length} 份材料 · 当前材料按版本归并，分析历史不重复计入待办</span><div className="flex items-center gap-3"><Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => update({ page: page - 1 })}>上一页</Button><span>{page} / {pageCount}</span><Button size="sm" variant="secondary" disabled={page >= pageCount} onClick={() => update({ page: page + 1 })}>下一页</Button></div></div>
    </> : null}
  </div>;
}
