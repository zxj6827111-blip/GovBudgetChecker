"use client";

import type { FormEvent, ReactNode } from "react";
import { useEffect, useState } from "react";
import {
  AlertCircle,
  Brain,
  Database,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";

import { getSeverityMeta } from "@/lib/issueSeverity";
import { cn } from "@/lib/utils";

type AnalysisJobSummary = {
  job_uuid: string;
  filename: string;
  display_title?: string;
  display_subtitle?: string;
  status: string;
  mode: string;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  error_message?: string;
  organization_name?: string;
  report_year?: number | string | null;
  ai_findings_count: number;
  rule_findings_count: number;
  merged_findings_count: number;
  structured_ingest_status?: string;
  structured_document_version_id?: number | null;
  structured_report_id?: string | null;
  elapsed_total_ms?: number | null;
};

type AnalysisJobListResponse = {
  available?: boolean;
  detail?: string;
  summary?: {
    total?: number;
    done?: number;
    processing?: number;
    queued?: number;
    error?: number;
    ai_findings_total?: number;
    rule_findings_total?: number;
  };
  items?: AnalysisJobSummary[];
};

type FindingItem = {
  id?: string;
  source?: string;
  rule_id?: string;
  severity?: string;
  severity_label?: string;
  title?: string;
  message?: string;
  suggestion?: string;
  page_number?: number | null;
  location?: {
    page?: number | null;
    table?: string | null;
    section?: string | null;
    row?: string | number | null;
  } | null;
  evidence?: Array<{
    page?: number | null;
    text?: string | null;
    text_snippet?: string | null;
  }>;
};

type AnalysisJobDetail = AnalysisJobSummary & {
  detail?: string;
  ai_findings?: FindingItem[];
  rule_findings?: FindingItem[];
  structured_ingest?: {
    status?: string;
    document_version_id?: number | null;
    facts_count?: number | null;
    recognized_tables?: number | null;
    ps_sync?: {
      report_id?: string | null;
      table_data_count?: number | null;
      line_item_count?: number | null;
    } | null;
  };
  result_meta?: {
    elapsed_ms?: {
      total?: number;
    };
  };
};

function formatDate(value?: string | null) {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(ms?: number | null) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms <= 0) {
    return "--";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)} s`;
  }
  return `${(ms / 60_000).toFixed(1)} min`;
}

function getJobTitle(job?: Pick<AnalysisJobSummary, "display_title" | "organization_name" | "filename" | "job_uuid"> | null) {
  return job?.display_title || job?.organization_name || job?.filename || job?.job_uuid || "";
}

function getJobSubtitle(job?: Pick<AnalysisJobSummary, "display_subtitle" | "organization_name" | "filename" | "job_uuid"> | null) {
  return job?.display_subtitle || job?.organization_name || job?.filename || job?.job_uuid || "";
}

function statusMeta(status?: string) {
  switch ((status || "").toLowerCase()) {
    case "done":
    case "completed":
      return { label: "已完成", className: "bg-emerald-100 text-emerald-700" };
    case "processing":
    case "running":
      return { label: "处理中", className: "bg-blue-100 text-blue-700" };
    case "queued":
      return { label: "排队中", className: "bg-amber-100 text-amber-700" };
    case "error":
    case "failed":
      return { label: "失败", className: "bg-red-100 text-red-700" };
    default:
      return { label: status || "未知", className: "bg-slate-100 text-slate-700" };
  }
}

function severityMeta(severity?: string) {
  const meta = getSeverityMeta(severity);
  return { label: meta.label, className: meta.panelClass };
  switch ((severity || "").toLowerCase()) {
    case "critical":
    case "high":
      return { label: "高", className: "bg-red-100 text-red-700" };
    case "medium":
      return { label: "中", className: "bg-amber-100 text-amber-700" };
    case "low":
    case "info":
      return { label: "低", className: "bg-blue-100 text-blue-700" };
    default:
      return { label: severity || "未知", className: "bg-slate-100 text-slate-700" };
  }
}

function buildLocationText(item: FindingItem) {
  const page = item.page_number || item.location?.page || item.evidence?.[0]?.page;
  const parts: string[] = [];
  if (page) {
    parts.push(`第 ${page} 页`);
  }
  if (item.location?.table) {
    parts.push(`表：${item.location.table}`);
  }
  if (item.location?.section) {
    parts.push(`章节：${item.location.section}`);
  }
  if (item.location?.row) {
    parts.push(`行：${item.location.row}`);
  }
  return parts.join(" / ") || "未记录定位信息";
}

function extractSnippet(item: FindingItem) {
  if (item.message?.trim()) {
    return item.message.trim();
  }
  const evidence = item.evidence?.find((entry) => entry?.text_snippet || entry?.text);
  return String(evidence?.text_snippet || evidence?.text || "").trim();
}

function FindingColumn({
  title,
  icon,
  accent,
  items,
  emptyText,
}: {
  title: string;
  icon: ReactNode;
  accent: string;
  items: FindingItem[];
  emptyText: string;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-4">
        <div className={cn("rounded-xl p-2", accent)}>{icon}</div>
        <div>
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          <p className="text-sm text-slate-500">共 {items.length} 条</p>
        </div>
      </div>
      <div className="max-h-[34rem] space-y-3 overflow-y-auto p-4">
        {items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">
            {emptyText}
          </div>
        ) : (
          items.map((item, index) => {
            const meta = severityMeta(item.severity_label || item.severity);
            const snippet = extractSnippet(item);
            return (
              <article
                key={item.id || `${title}-${index}`}
                className="rounded-xl border border-slate-200 bg-slate-50/80 p-4"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", meta.className)}>
                    {meta.label}
                  </span>
                  {item.rule_id ? (
                    <span className="rounded-full bg-slate-200 px-2.5 py-1 font-mono text-[11px] text-slate-700">
                      {item.rule_id}
                    </span>
                  ) : null}
                </div>
                <h4 className="mt-3 text-sm font-semibold text-slate-900">
                  {item.title || item.id || "未命名问题"}
                </h4>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  {snippet || "没有返回问题说明。"}
                </p>
                <div className="mt-3 text-xs text-slate-500">{buildLocationText(item)}</div>
                {item.suggestion ? (
                  <div className="mt-3 rounded-lg bg-white px-3 py-2 text-xs text-slate-600">
                    建议：{item.suggestion}
                  </div>
                ) : null}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}

export default function AnalysisResultsPanel() {
  const [jobs, setJobs] = useState<AnalysisJobSummary[]>([]);
  const [selectedJobUuid, setSelectedJobUuid] = useState("");
  const [detail, setDetail] = useState<AnalysisJobDetail | null>(null);
  const [listSummary, setListSummary] = useState<AnalysisJobListResponse["summary"] | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [modeFilter, setModeFilter] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadList() {
      setLoadingList(true);
      setError("");

      try {
        const params = new URLSearchParams();
        params.set("limit", "50");
        if (search.trim()) {
          params.set("search", search.trim());
        }
        if (statusFilter) {
          params.set("status", statusFilter);
        }
        if (modeFilter) {
          params.set("mode", modeFilter);
        }

        const response = await fetch(`/api/admin/analysis-results?${params.toString()}`, {
          cache: "no-store",
        });
        const payload = (await response.json()) as AnalysisJobListResponse;
        if (!response.ok) {
          throw new Error(payload.detail || "无法加载分析结果列表");
        }

        if (cancelled) {
          return;
        }

        const nextJobs = Array.isArray(payload.items) ? payload.items : [];
        setJobs(nextJobs);
        setListSummary(payload.summary ?? null);
        setSelectedJobUuid((current) => {
          if (current && nextJobs.some((item) => item.job_uuid === current)) {
            return current;
          }
          return nextJobs[0]?.job_uuid || "";
        });
        if (payload.available === false) {
          setError(payload.detail || "数据库尚未就绪，暂时无法读取分析结果。");
        }
      } catch (fetchError) {
        if (!cancelled) {
          setJobs([]);
          setListSummary(null);
          setSelectedJobUuid("");
          setDetail(null);
          setError(fetchError instanceof Error ? fetchError.message : "无法加载分析结果列表");
        }
      } finally {
        if (!cancelled) {
          setLoadingList(false);
        }
      }
    }

    void loadList();
    return () => {
      cancelled = true;
    };
  }, [search, statusFilter, modeFilter, reloadToken]);

  useEffect(() => {
    let cancelled = false;

    async function loadDetail() {
      if (!selectedJobUuid) {
        setDetail(null);
        return;
      }

      setLoadingDetail(true);
      try {
        const response = await fetch(`/api/admin/analysis-results/${encodeURIComponent(selectedJobUuid)}`, {
          cache: "no-store",
        });
        const payload = (await response.json()) as AnalysisJobDetail;
        if (!response.ok) {
          throw new Error(payload.detail || "无法加载分析结果详情");
        }
        if (!cancelled) {
          setDetail(payload);
        }
      } catch (fetchError) {
        if (!cancelled) {
          setDetail(null);
          setError(fetchError instanceof Error ? fetchError.message : "无法加载分析结果详情");
        }
      } finally {
        if (!cancelled) {
          setLoadingDetail(false);
        }
      }
    }

    void loadDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedJobUuid]);

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch(searchDraft);
  }

  const aiFindings = Array.isArray(detail?.ai_findings) ? detail.ai_findings : [];
  const ruleFindings = Array.isArray(detail?.rule_findings) ? detail.rule_findings : [];
  const selectedSummary = jobs.find((item) => item.job_uuid === selectedJobUuid) ?? null;
  const status = statusMeta(detail?.status || selectedSummary?.status);

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">分析结果中心</h2>
            <p className="mt-1 text-sm text-slate-600">
              查看数据库中的分析任务、AI 结果、规则结果与结构化入库信息。
            </p>
          </div>
          <form onSubmit={handleSearchSubmit} className="flex flex-col gap-3 md:flex-row">
            <div className="relative min-w-[240px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="搜索任务 ID、文件名或单位名"
                className="w-full rounded-xl border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
            >
              <option value="">全部状态</option>
              <option value="done">已完成</option>
              <option value="processing">处理中</option>
              <option value="queued">排队中</option>
              <option value="error">失败</option>
            </select>
            <select
              value={modeFilter}
              onChange={(event) => setModeFilter(event.target.value)}
              className="rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
            >
              <option value="">全部模式</option>
              <option value="dual">dual</option>
              <option value="legacy">legacy</option>
            </select>
            <button
              type="submit"
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              <Search className="h-4 w-4" />
              查询
            </button>
          </form>
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">任务总数</div><div className="mt-2 text-2xl font-semibold text-slate-900">{listSummary?.total ?? jobs.length}</div></div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">已完成</div><div className="mt-2 text-2xl font-semibold text-emerald-600">{listSummary?.done ?? 0}</div></div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">处理中</div><div className="mt-2 text-2xl font-semibold text-blue-600">{(listSummary?.processing ?? 0) + (listSummary?.queued ?? 0)}</div></div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">失败任务</div><div className="mt-2 text-2xl font-semibold text-rose-600">{listSummary?.error ?? 0}</div></div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">AI Findings</div><div className="mt-2 text-2xl font-semibold text-slate-900">{listSummary?.ai_findings_total ?? 0}</div></div>
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">Rule Findings</div><div className="mt-2 text-2xl font-semibold text-slate-900">{listSummary?.rule_findings_total ?? 0}</div></div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
            <div>
              <h3 className="text-base font-semibold text-slate-900">任务列表</h3>
              <p className="text-sm text-slate-500">按最近更新时间排序</p>
            </div>
            <button
              type="button"
              onClick={() => setReloadToken((current) => current + 1)}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 transition hover:bg-slate-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", loadingList ? "animate-spin" : "")} />
              刷新
            </button>
          </div>
          <div className="max-h-[52rem] overflow-y-auto p-3">
            {loadingList ? (
              <div className="flex items-center justify-center gap-2 px-4 py-10 text-sm text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载任务列表...
              </div>
            ) : jobs.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                当前没有可展示的分析任务。
              </div>
            ) : (
              jobs.map((item) => {
                const isSelected = item.job_uuid === selectedJobUuid;
                const itemStatus = statusMeta(item.status);
                return (
                  <button
                    key={item.job_uuid}
                    type="button"
                    onClick={() => setSelectedJobUuid(item.job_uuid)}
                    className={cn(
                      "mb-3 w-full rounded-2xl border px-4 py-4 text-left transition",
                      isSelected
                        ? "border-slate-900 bg-slate-900 text-white shadow-lg"
                        : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold">{getJobTitle(item)}</div>
                        <div className={cn("mt-1 truncate text-xs", isSelected ? "text-slate-300" : "text-slate-500")}>
                          {getJobSubtitle(item)}
                        </div>
                      </div>
                      <span className={cn("rounded-full px-2 py-1 text-[11px] font-medium", isSelected ? "bg-white/15 text-white" : itemStatus.className)}>
                        {itemStatus.label}
                      </span>
                    </div>
                    <div className={cn("mt-4 grid grid-cols-3 gap-2 text-center text-xs", isSelected ? "text-slate-200" : "text-slate-600")}>
                      <div className="rounded-xl bg-black/5 px-2 py-2"><div className="font-semibold">{item.ai_findings_count}</div><div className="mt-1 text-[11px] opacity-80">AI</div></div>
                      <div className="rounded-xl bg-black/5 px-2 py-2"><div className="font-semibold">{item.rule_findings_count}</div><div className="mt-1 text-[11px] opacity-80">Rule</div></div>
                      <div className="rounded-xl bg-black/5 px-2 py-2"><div className="font-semibold">{item.merged_findings_count}</div><div className="mt-1 text-[11px] opacity-80">Merged</div></div>
                    </div>
                    <div className={cn("mt-3 text-xs", isSelected ? "text-slate-300" : "text-slate-500")}>
                      更新时间：{formatDate(item.completed_at || item.updated_at || item.created_at)}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>

        <section className="space-y-6">
          {!selectedJobUuid ? (
            <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white px-6 text-center text-slate-500 shadow-sm">
              <Database className="mb-4 h-10 w-10 text-slate-300" />
              <h3 className="text-lg font-medium text-slate-700">请选择一条分析任务</h3>
              <p className="mt-2 max-w-md text-sm">左侧列表会展示已入库的分析任务，点击后即可查看 AI、规则和结构化结果。</p>
            </div>
          ) : loadingDetail ? (
            <div className="flex min-h-[24rem] items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-white text-slate-500 shadow-sm">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在加载结果详情...
            </div>
          ) : detail ? (
            <>
              <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-xl font-semibold text-slate-900">{getJobTitle(detail)}</h3>
                      <span className={cn("rounded-full px-2.5 py-1 text-xs font-medium", status.className)}>
                        {status.label}
                      </span>
                      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">
                        {detail.mode || "unknown"}
                      </span>
                    </div>
                    <p className="mt-2 text-sm text-slate-500">{getJobSubtitle(detail)}</p>
                    <p className="mt-2 break-all font-mono text-xs text-slate-400">{detail.job_uuid}</p>
                    <div className="mt-3 grid gap-2 text-sm text-slate-600 md:grid-cols-2">
                      <div>单位：{detail.organization_name || "--"}</div>
                      <div>年度：{detail.report_year || "--"}</div>
                      <div>开始时间：{formatDate(detail.started_at || detail.created_at)}</div>
                      <div>完成时间：{formatDate(detail.completed_at || detail.updated_at)}</div>
                    </div>
                    {detail.error_message ? (
                      <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        失败原因：{detail.error_message}
                      </div>
                    ) : null}
                  </div>
                  <div className="grid min-w-[280px] grid-cols-2 gap-3">
                    <div className="rounded-2xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-500">AI Findings</div><div className="mt-2 text-xl font-semibold text-slate-900">{detail.ai_findings_count}</div></div>
                    <div className="rounded-2xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-500">Rule Findings</div><div className="mt-2 text-xl font-semibold text-slate-900">{detail.rule_findings_count}</div></div>
                    <div className="rounded-2xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-500">Merged</div><div className="mt-2 text-xl font-semibold text-slate-900">{detail.merged_findings_count}</div></div>
                    <div className="rounded-2xl bg-slate-50 px-4 py-3"><div className="text-xs text-slate-500">总耗时</div><div className="mt-2 text-xl font-semibold text-slate-900">{formatDuration(detail.result_meta?.elapsed_ms?.total || detail.elapsed_total_ms)}</div></div>
                  </div>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">结构化状态</div><div className="mt-2 text-lg font-semibold text-slate-900">{detail.structured_ingest?.status || detail.structured_ingest_status || "--"}</div></div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">文档版本 ID</div><div className="mt-2 text-lg font-semibold text-slate-900">{detail.structured_ingest?.document_version_id || detail.structured_document_version_id || "--"}</div></div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">PS Report ID</div><div className="mt-2 break-all text-sm font-semibold text-slate-900">{detail.structured_ingest?.ps_sync?.report_id || detail.structured_report_id || "--"}</div></div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-xs text-slate-500">结构化 Facts</div><div className="mt-2 text-lg font-semibold text-slate-900">{detail.structured_ingest?.facts_count ?? "--"}</div></div>
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <FindingColumn
                  title="AI Findings"
                  icon={<Brain className="h-5 w-5 text-indigo-600" />}
                  accent="bg-indigo-50"
                  items={aiFindings}
                  emptyText="当前任务没有 AI Findings。"
                />
                <FindingColumn
                  title="Rule Findings"
                  icon={<ShieldCheck className="h-5 w-5 text-emerald-600" />}
                  accent="bg-emerald-50"
                  items={ruleFindings}
                  emptyText="当前任务没有 Rule Findings。"
                />
              </div>
            </>
          ) : (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center text-sm text-slate-500 shadow-sm">
              当前任务暂时没有可展示的详情。
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
