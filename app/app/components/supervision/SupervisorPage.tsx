"use client";

import Link from "next/link";
import type { Route } from "next";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Download, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui";
import { ReportingTaskEntry } from "../materials/ReportingTaskEntry";
import { defaultSupervisorPeriod, displayCount, labelReview, missingDepartments, organizationPath, percent, readSupervisorData, reportRatio, severityLabel,
  type Department, type Finding, type Material, type SupervisorData, type SupervisorView } from "./supervisionAdapters";

const names: Record<SupervisorView, string> = { overview: "主管部门总览", materials: "报送台账", issues: "问题分析", remediation: "整改追踪" };
const inputClass = "min-w-0 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700";
function Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return <section className="min-w-0 rounded-card border border-border bg-white p-4 shadow-ui"><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold text-slate-900">{title}</h2>{action}</div>{children}</section>;
}
function Metric({ label, value, context, href, tone = "text-slate-900" }: { label: string; value: ReactNode; context: string; href: string; tone?: string }) {
  return <Link href={href as Route} className="rounded-card border border-border bg-white px-4 py-3 shadow-ui hover:border-primary-300"><div className="text-xs text-slate-500">{label}</div><strong className={`my-2 block text-[28px] font-semibold tabular-nums ${tone}`}>{value}</strong><div className="text-xs text-slate-400">{context}</div></Link>;
}
function MaterialTable({ rows, href, emptyText = "当前筛选没有材料记录" }: { rows: Material[]; href: (id: string) => string; emptyText?: string }) {
  return <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-surface-100 text-slate-500"><tr>{["部门 / 主体单位", "材料", "报送", "确认问题", "待复核", "整改登记", "复核 / 截止"].map(t => <th key={t} className="whitespace-nowrap px-3 py-2 font-normal">{t}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.slot_id} className="border-b border-border"><td className="px-3 py-3"><span className="block text-slate-400">{row.department_name}</span><Link className="text-primary-700" href={href(row.slot_id) as Route}>{row.organization_name}</Link></td><td className="px-3 py-3">{row.fiscal_year} 年{row.report_kind === "budget" ? "预算" : "决算"}<span className="mt-1 block max-w-[210px] text-slate-400">{row.filename || "尚无文件"}</span></td><td className="whitespace-nowrap px-3 py-3">{row.applicability_status === "not_applicable" ? "不适用" : row.uploaded ? "已上传" : row.overdue ? "逾期未上传" : "尚未上传"}</td><td className="px-3 py-3 tabular-nums text-danger-600">{displayCount(row.confirmed_count)}</td><td className="px-3 py-3 tabular-nums">{displayCount(row.pending_count)} 条线索<span className="block text-slate-400">{displayCount(row.pending_checks)} 项补核待办</span></td><td className="whitespace-nowrap px-3 py-3">{row.rounds_count ? `${row.rounds_count} 轮` : "未登记"}{row.history_incomplete ? <span className="block text-warning-700">历史不完整</span> : null}</td><td className="whitespace-nowrap px-3 py-3">{labelReview(row.review_status)}<span className="block text-slate-400">{row.due_at ? new Date(row.due_at).toLocaleDateString("zh-CN") : "截止时间未知"}</span></td></tr>)}</tbody></table>{!rows.length ? <p className="py-6 text-center text-sm text-slate-400">{emptyText}</p> : null}</div>;
}

function LegacySupervisorPage({ view }: { view: SupervisorView }) {
  const router = useRouter(), pathname = usePathname(), search = useSearchParams();
  const queryString = search.toString();
  const latestQuery = useRef(queryString);
  useEffect(() => { latestQuery.current = queryString; }, [queryString]);
  const year = Number(search.get("fiscal_year")) || new Date().getFullYear();
  const kind = search.get("report_kind") === "final" ? "final" : "budget", scope = search.get("scope_id") || "";
  const [storedData, setData] = useState<SupervisorData | null>(null), [loadedQuery, setLoadedQuery] = useState("");
  const [error, setError] = useState<string | null>(null), [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0), [top, setTop] = useState<5 | 10>(5), [keyword, setKeyword] = useState(search.get("keyword") || "");
  const appliedKeyword = search.get("keyword") || "";
  useEffect(() => { setKeyword(appliedKeyword); }, [appliedKeyword]);
  const [baselineNote, setBaselineNote] = useState(""), [baselineBusy, setBaselineBusy] = useState(false);
  const mode = search.get("mode") === "materials" ? "materials" : "departments";
  const apiQuery = useMemo(() => {
    const params = new URLSearchParams(queryString);
    params.set("fiscal_year", String(year)); params.set("report_kind", kind);
    params.set("view", view === "materials" ? mode : view);
    params.delete("mode"); return params.toString();
  }, [queryString, year, kind, view, mode]);
  // URL 改变后的首次渲染不能把上一范围/层级的数据当成本次响应。
  const data = loadedQuery === apiQuery ? storedData : null;
  const defaultPeriod = useMemo(() => data ? defaultSupervisorPeriod(new URLSearchParams(queryString), data.meta.available_periods || []) : null, [data, queryString]);
  const update = useCallback((fields: Record<string, string>) => {
    // 连续切换范围、年度和文种时，后一次选择不能覆盖前一次尚未提交的导航。
    const params = new URLSearchParams(latestQuery.current); params.delete("page");
    for (const [key, value] of Object.entries(fields)) { if (value) params.set(key, value); else params.delete(key); }
    latestQuery.current = params.toString();
    router.replace(`${pathname}?${params}` as Route);
  }, [pathname, router]);
  useEffect(() => { if (defaultPeriod) update(defaultPeriod); }, [defaultPeriod, update]);
  function href(page: SupervisorView, fields: Record<string, string> = {}) {
    const params = new URLSearchParams({legacy:"1"});
    params.set("fiscal_year", String(year)); params.set("report_kind", kind); if (scope) params.set("scope_id", scope);
    for (const [key, value] of Object.entries(fields)) if (value) params.set(key, value);
    return `/supervision${page === "overview" ? "" : "/" + page}?${params}`;
  }
  const detailHref = (id: string) => `/supervision/materials/${encodeURIComponent(id)}?fiscal_year=${year}&report_kind=${kind}${scope ? "&scope_id=" + encodeURIComponent(scope) : ""}`;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setData(null); setError(null);
    fetch(`/api/supervision?${apiQuery}`, { cache: "no-store", signal: controller.signal }).then(async response => {
      if (response.status === 401) { router.replace(`/login?next=${encodeURIComponent(pathname + "?" + queryString)}` as Route); throw new Error("登录已失效，请重新登录"); }
      const payload = await response.json(); if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "主管统计读取失败");
      return readSupervisorData(payload);
    }).then(payload => { if (!controller.signal.aborted) { setData(payload); setLoadedQuery(apiQuery); } }).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [apiQuery, refresh, router, pathname, queryString]);
  useEffect(() => {
    if (!data || scope) return;
    const ids = new Set(data.meta.organizations.map(o => o.id)), roots = data.meta.organizations.filter(o => !o.parent_id || !ids.has(o.parent_id));
    if (roots.length === 1) update({ scope_id: roots[0].id });
    // 只在账号存在唯一查看根节点时选择，不猜多个授权范围中的某一个。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.meta.organizations, scope]);
  async function confirmBaseline() {
    if (!data || !baselineNote.trim()) return;
    setBaselineBusy(true); setError(null);
    try {
      const response = await fetch("/api/supervision/baselines", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope_id: scope, fiscal_year: year, report_kind: kind, preview_token: data.meta.baseline_preview_token, note: baselineNote.trim() }) });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "确认失败");
      setBaselineNote(""); setRefresh(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "确认失败"); } finally { setBaselineBusy(false); }
  }
  const summary = data?.summary;
  return <div className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6" data-testid="gbc-supervisor-page">
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold tracking-tight text-slate-900">{names[view]}</h1><p className="mt-1 text-sm text-slate-500">{scope && data ? organizationPath(scope, data.meta.organizations) : "全部授权范围"} · 部门本级及所属单位</p></div><div className="flex gap-2"><Button variant="secondary" onClick={() => setRefresh(n => n + 1)} disabled={loading}><RefreshCw className="h-4 w-4" />刷新</Button><a className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700" href={`/api/supervision?${apiQuery}&format=csv`}><Download className="h-4 w-4" />导出当前清单</a></div></div>
    <div className="mb-4 flex flex-wrap items-end gap-3 rounded-card border border-border bg-white p-3 text-xs text-slate-500">
      <label className="flex min-w-0 flex-col gap-1">查看范围<select className={`${inputClass} max-w-[380px]`} value={scope} onChange={e => update({ scope_id: e.target.value, department_id: "" })}><option value="">全部授权范围</option>{data?.meta.organizations.map(o => <option key={o.id} value={o.id}>{organizationPath(o.id, data.meta.organizations)}</option>)}</select></label>
      <label className="flex flex-col gap-1">财政年度<select className={inputClass} value={year} onChange={e => update({ fiscal_year: e.target.value })}>{Array.from({ length: new Date().getFullYear() + 2 - 2000 }, (_, i) => new Date().getFullYear() + 1 - i).map(y => <option key={y}>{y}</option>)}</select></label>
      <label className="flex flex-col gap-1">材料类型<select className={inputClass} value={kind} onChange={e => update({ report_kind: e.target.value })}><option value="budget">预算</option><option value="final">决算</option></select></label>
      {data ? <span className="ml-auto">{data.meta.is_read_only ? "主管只读账号" : "工作人员查看"} · {new Date(data.meta.generated_at).toLocaleString("zh-CN", { hour12: false })}</span> : null}
    </div>
    {error ? <div role="alert" className="mb-4 rounded-card border border-danger-200 bg-danger-50 p-4 text-sm text-danger-700">{error}<Button variant="secondary" size="sm" className="ml-3" onClick={() => setRefresh(n => n + 1)}>重试</Button></div> : null}
    {loading || defaultPeriod ? <p className="py-12 text-center text-sm text-slate-400">{defaultPeriod ? "正在选择已有台账的年度和材料类型…" : "正在读取主管统计…"}</p> : null}
    {data && summary && !defaultPeriod ? <>
      {!summary.material_count ? <div role="status" className="mb-4 rounded-md border border-info-200 bg-info-50 p-3 text-sm text-info-800"><p>当前查看范围内没有 {year} 年{kind === "budget" ? "预算" : "决算"}台账记录。</p>{data.meta.available_periods?.length ? <div className="mt-2 flex flex-wrap items-center gap-2"><span>查看已建档材料：</span>{data.meta.available_periods.slice(0, 6).map(period => <Button key={`${period.fiscal_year}:${period.report_kind}`} variant="secondary" size="sm" onClick={() => update({ fiscal_year: String(period.fiscal_year), report_kind: period.report_kind, department_id: "", keyword: "", category: "", severity: "", status: "" })}>{period.fiscal_year} 年{period.report_kind === "budget" ? "预算" : "决算"} · {period.material_count} 份</Button>)}</div> : null}</div> : null}
      {!data.meta.baseline_ready ? <p className="mb-4 rounded-md border border-warning-200 bg-warning-50 p-3 text-xs text-warning-800">应交清单尚未确认，当前仅展示已建档材料；应交总量、缺交部门和报送比例保持未知。已知逾期未上传材料 {summary.overdue_missing} 份。</p> : <p className="mb-3 text-xs text-slate-400">应交清单由 {data.meta.baseline_by} 于 {data.meta.baseline_at ? new Date(data.meta.baseline_at).toLocaleString("zh-CN") : "—"} 确认；统计只取当前有效材料版本。</p>}
      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="材料已交 / 应交" value={<>{data.meta.baseline_ready ? summary.received_expected : summary.uploaded}<span className="text-lg font-normal text-slate-400"> / {displayCount(summary.expected)}</span></>} context={`${summary.department_count} 个部门 · ${summary.unit_count} 个单位`} href={href("materials")} />
        <Metric label="未交齐部门" value={displayCount(missingDepartments(data))} context={`完全未交 ${displayCount(summary.empty_departments)} · 部分未交 ${displayCount(summary.partial_departments)}`} href={href("materials", { status: "missing" })} tone="text-danger-600" />
        <Metric label="人工确认问题" value={summary.analyzed ? summary.confirmed : "—"} context={`另有 ${summary.pending} 条待复核线索 · ${summary.unknown_analysis} 份尚无法判断`} href={href("issues")} tone="text-danger-600" />
        <Metric label="多轮整改仍有问题" value={summary.repeat_materials} context={`按材料统计 · ${summary.unregistered_history} 份历史未完整登记`} href={href("remediation", { status: "repeat" })} tone="text-warning-700" />
      </div>
      <div className="mb-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500"><span>已完成分析 {summary.analyzed} 份</span><span>已完成人工复核 {summary.reviewed} 份</span><span>处理失败 {summary.failed} 份</span><span>未完成补核 {summary.pending_checks} 项</span><span>{summary.unknown_coverage} 份分析的检查覆盖未知</span></div>
      {view === "overview" ? <>
        <div className="mb-4 grid gap-4 lg:grid-cols-2"><Panel title="材料报送进度"><div className="flex items-center gap-5"><div className="relative h-28 w-28 shrink-0"><svg viewBox="0 0 112 112" className="h-28 w-28" role="img" aria-label={`报送比例 ${percent(reportRatio(data))}`}><circle cx="56" cy="56" r="43" fill="none" strokeWidth="10" className="stroke-surface-100" /><circle cx="56" cy="56" r="43" fill="none" strokeWidth="10" strokeDasharray={`${270.18 * (reportRatio(data) ?? 0)} 270.18`} transform="rotate(-90 56 56)" className="stroke-primary-600" /></svg><div className="absolute inset-0 flex flex-col items-center justify-center"><strong className="text-xl font-semibold text-slate-900">{percent(reportRatio(data))}</strong><span className="text-xs text-slate-400">已交比例</span></div></div><dl className="grid flex-1 grid-cols-2 gap-3 text-xs"><div><dt className="text-slate-400">应交材料</dt><dd className="mt-1 text-lg text-slate-900">{displayCount(summary.expected)}</dd></div><div><dt className="text-slate-400">尚缺材料</dt><dd className="mt-1 text-lg text-danger-600">{displayCount(summary.missing)}</dd></div><div><dt className="text-slate-400">逾期未上传</dt><dd className="mt-1 text-lg text-warning-700">{summary.overdue_missing}</dd></div><div><dt className="text-slate-400">待完成复核</dt><dd className="mt-1 text-lg text-slate-900">{Math.max(0, summary.uploaded - summary.reviewed)}</dd></div></dl></div></Panel>
          <Panel title="问题集中在哪里" action={<Link className="text-xs text-primary-700" href={href("issues") as Route}>全部问题 →</Link>}><div className="space-y-3">{summary.categories.map(item => <Link key={item.name} href={href("issues", { category: item.name }) as Route} className="grid grid-cols-[120px_minmax(0,1fr)_35px] items-center gap-3 text-xs"><span className="text-slate-600">{item.name}</span><span className="h-2 overflow-hidden rounded bg-surface-100"><span className="block h-full rounded bg-info-500" style={{ width: `${item.count / Math.max(1, ...summary.categories.map(c => c.count)) * 100}%` }} /></span><span className="text-right tabular-nums text-slate-900">{item.count}</span></Link>)}{!summary.categories.length ? <p className="py-5 text-center text-xs text-slate-400">暂无可分类的人工确认问题</p> : null}</div></Panel></div>
        <div className="mb-4 grid gap-4 lg:grid-cols-2"><Panel title="确认问题最多的部门" action={<div className="flex items-center gap-3"><select aria-label="部门排名展示数量" className={inputClass} value={top} onChange={e => setTop(Number(e.target.value) as 5 | 10)}><option value={5}>前 5 个</option><option value={10}>前 10 个</option></select><Link className="text-xs text-primary-700" href={href("materials", { status: "issues" }) as Route}>完整排名 →</Link></div>}><div>{data.ranking.filter(d => d.confirmed > 0).slice(0, top).map((d, index) => <Link key={d.id} href={href("materials", { mode: "materials", department_id: d.id }) as Route} className="flex items-center gap-3 border-b border-border py-3 text-sm"><span className="text-xs text-slate-400">{index + 1}</span><span className="flex-1">{d.name}<span className="mt-1 block text-xs text-slate-400">已复核 {d.reviewed} 份 · 问题材料占比 {percent(d.problem_rate)}</span></span><strong className="font-semibold text-danger-600">{d.confirmed} 条</strong></Link>)}{!data.ranking.some(d => d.confirmed) ? <p className="py-5 text-center text-xs text-slate-400">暂无人工确认问题排名</p> : null}</div></Panel>
          <Panel title="重点整改材料" action={<Link className="text-xs text-primary-700" href={href("remediation") as Route}>完整整改追踪 →</Link>}>{data.priority.map(m => <Link key={m.slot_id} href={detailHref(m.slot_id) as Route} className="flex items-center justify-between gap-3 border-b border-border py-3 text-sm"><span>{m.organization_name}<span className="mt-1 block text-xs text-slate-400">{m.fiscal_year} 年{m.report_kind === "budget" ? "预算" : "决算"} · 已登记 {m.rounds_count} 轮</span></span><span className="whitespace-nowrap text-danger-600">仍有 {displayCount(m.confirmed_count)} 条</span></Link>)}{!data.priority.length ? <p className="py-5 text-center text-xs text-slate-400">暂无已登记多轮整改且仍有确认问题的材料</p> : null}</Panel></div>
      </> : <Panel title={names[view]} action={<span className="text-xs text-slate-400">当前筛选 {data.list.total} 条</span>}>
        <div className="mb-4 flex flex-wrap gap-2"><form className="flex min-w-0 flex-1 gap-2" onSubmit={e => { e.preventDefault(); update({ keyword }); }}><input aria-label="搜索部门单位或问题" className={`${inputClass} w-full`} value={keyword} onChange={e => setKeyword(e.target.value)} placeholder={view === "issues" ? "搜索单位或问题标题" : mode === "departments" && view === "materials" ? "搜索主管部门" : "搜索部门、单位或材料"} /><Button variant="secondary" size="sm" type="submit"><Search className="h-4 w-4" />搜索</Button></form>
          {view === "materials" ? <select className={inputClass} aria-label="报送台账展示层级" value={mode} onChange={e => update({ mode: e.target.value, keyword: "" })}><option value="departments">部门汇总</option><option value="materials">单位与材料</option></select> : null}
          {view !== "issues" ? <select className={inputClass} aria-label="筛选报送整改状态" value={search.get("status") || "all"} onChange={e => update({ status: e.target.value })}><option value="all">全部</option><option value="missing">未上传</option><option value="overdue">逾期未上传</option><option value="issues">存在确认问题</option><option value="repeat">多轮未闭环</option><option value="waiting">已提交待复验</option></select> : <select className={inputClass} aria-label="问题严重程度" value={search.get("severity") || "all"} onChange={e => update({ severity: e.target.value })}><option value="all">全部严重程度</option>{["critical", "high", "medium", "low", "manual_review"].map(s => <option key={s} value={s}>{severityLabel(s)}</option>)}</select>}
        {view === "materials" && mode === "departments" ? <select className={inputClass} aria-label="部门排名排序" value={search.get("sort") || "confirmed"} onChange={e => update({ sort: e.target.value })}><option value="confirmed">确认问题数量</option><option value="rate">问题材料占比</option><option value="severe">严重问题数量</option></select> : null}
          {view !== "materials" || mode === "materials" ? <select className={`${inputClass} max-w-[320px]`} aria-label="清单部门筛选" value={search.get("department_id") || ""} onChange={e => update({ department_id: e.target.value })}><option value="">全部部门</option>{data.departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select> : null}
          {view === "issues" ? <select className={inputClass} aria-label="问题类别筛选" value={search.get("category") || ""} onChange={e => update({ category: e.target.value })}><option value="">全部问题类别</option>{summary.categories.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}</select> : null}
          {search.get("category") ? <Button variant="ghost" size="sm" onClick={() => update({ category: "" })}>清除“{search.get("category")}”筛选</Button> : null}
        </div>
        {search.get("department_id") ? <p className="mb-3 text-xs text-slate-500">当前清单仅查看：{data.departments.find(d => d.id === search.get("department_id"))?.name || "所选部门"}；上方指标为整个查看范围。<button className="ml-2 text-primary-700" onClick={() => update({ department_id: "" })}>清除部门筛选</button></p> : null}
        {view === "materials" && mode === "departments" ? <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-surface-100 text-slate-500"><tr>{["主管部门", "已交 / 应交", "尚缺", "确认问题", "已知严重问题", "问题材料占比", "反复整改材料"].map(t => <th key={t} className="whitespace-nowrap px-3 py-2 font-normal">{t}</th>)}</tr></thead><tbody>{(data.list.items as Department[]).map(d => <tr key={d.id} className="border-b border-border"><td className="px-3 py-3"><Link className="text-primary-700" href={href("materials", { mode: "materials", department_id: d.id }) as Route}>{d.name}</Link></td><td className="whitespace-nowrap px-3 py-3">{data.meta.baseline_ready ? d.received_expected : d.uploaded} / {displayCount(d.expected)}</td><td className="px-3 py-3">{displayCount(d.missing)}</td><td className="px-3 py-3 text-danger-600">{d.confirmed}</td><td className="px-3 py-3">{d.severe}</td><td className="px-3 py-3">{percent(d.problem_rate)}<span className="block text-slate-400">样本 {d.reviewed} 份</span></td><td className="px-3 py-3">{d.repeat_materials} 份<span className="block text-slate-400">最高登记 {d.max_rounds} 轮</span></td></tr>)}</tbody></table></div>
          : view === "issues" ? <div className="space-y-3">{(data.list.items as Finding[]).map(f => <Link key={`${f.slot_id}:${f.issue_id}`} href={detailHref(f.slot_id!) as Route} className="block rounded-md border border-border p-3"><div className="flex flex-wrap justify-between gap-2"><strong className="text-sm font-medium text-slate-900">{f.title}</strong><span className="text-xs text-danger-600">{severityLabel(f.severity)}</span></div><p className="mt-1 text-xs text-slate-500">{f.department_name} / {f.organization_name} · {f.category}</p></Link>)}{!data.list.total ? <p className="py-6 text-center text-sm text-slate-400">当前筛选没有可展示的人工确认问题</p> : null}</div>
          : <MaterialTable rows={data.list.items as Material[]} href={detailHref} emptyText={view === "remediation" ? "当前暂无正式登记或历史不完整的整改材料" : undefined} />}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>第 {data.list.total_pages ? data.list.page : 0} / {data.list.total_pages} 页 · 共 {data.list.total} 条</span><div className="flex items-center gap-2"><select aria-label="每页展示数量" className={inputClass} value={data.list.page_size} onChange={e => update({ page_size: e.target.value })}><option value={20}>20 条/页</option><option value={50}>50 条/页</option></select><Button variant="secondary" size="sm" disabled={data.list.page <= 1} onClick={() => update({ page: String(data.list.page - 1) })}>上一页</Button><Button variant="secondary" size="sm" disabled={data.list.page >= data.list.total_pages} onClick={() => update({ page: String(data.list.page + 1) })}>下一页</Button></div></div>
      </Panel>}
      {view === "materials" && !data.meta.is_read_only && scope ? <details className="mt-4 rounded-card border border-border bg-white p-4 text-sm"><summary className="text-slate-700">工作人员：确认本范围、本年度的应交清单</summary><p className="my-3 text-xs text-slate-500">先在材料台账导入并核对完整清单。此操作确认整个选定范围的应交材料，不以当前搜索或分页作为应交清单。新增或删除材料后需要重新确认。</p><Link className="text-xs text-primary-700" href="/materials">打开材料台账完善清单 →</Link><textarea aria-label="应交清单确认依据" className={`${inputClass} mt-3 block w-full`} placeholder="填写确认依据，例如已与本年度完整单位报送清单逐项核对。" value={baselineNote} onChange={e => setBaselineNote(e.target.value)} /><Button className="mt-3" variant="secondary" disabled={baselineBusy || baselineNote.trim().length < 4 || !data.meta.baseline_preview_token} onClick={confirmBaseline}>确认应交清单</Button></details> : null}
    </> : null}
  </div>;
}

export function SupervisorPage({ view }: { view: SupervisorView }) {
  return <ReportingTaskEntry legacy={<LegacySupervisorPage view={view}/>} supervisor view={view === "materials" ? "departments" : view}/>;
}
