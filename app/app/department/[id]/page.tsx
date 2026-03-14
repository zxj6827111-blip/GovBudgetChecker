"use client";

import type { Route } from "next";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  Filter,
  UploadCloud,
  Download,
  RefreshCw,
  MoreHorizontal,
  History,
  Eye,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { JobSummaryRecord, OrganizationRecord } from "@/lib/uiAdapters";
import {
  formatDateTime,
  getDisplayIssueTotal,
  getHighRiskCount,
  normalizeUiTaskStatus,
  toUiTask,
} from "@/lib/uiAdapters";

type JobsResponse = {
  jobs?: JobSummaryRecord[];
};

type OrganizationsResponse = {
  organizations?: OrganizationRecord[];
};

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return fallback;
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

function normalizeSearchValue(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function getSearchStatusLabel(status: ReturnType<typeof normalizeUiTaskStatus>): string {
  if (status === "completed") {
    return "已完成 completed";
  }
  if (status === "failed") {
    return "失败 failed";
  }
  return "分析中 analyzing";
}

export default function Department() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const normalizedSearchQuery = useMemo(
    () => normalizeSearchValue(searchQuery),
    [searchQuery],
  );
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [jobs, setJobs] = useState<JobSummaryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [includeSub, setIncludeSub] = useState(true);
  const [activeTab, setActiveTab] = useState<"budget" | "final">("budget");
  const [selectedYear, setSelectedYear] = useState("all");
  const [selectedStatus, setSelectedStatus] = useState("all");
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenMenuId(null);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);
      const [orgPayload, jobsPayload] = await Promise.all([
        fetchJson<OrganizationsResponse>("/api/organizations/list", { organizations: [] }),
        fetchJson<JobsResponse>(
          `/api/organizations/${encodeURIComponent(id)}/jobs?include_children=${includeSub ? "true" : "false"}`,
          { jobs: [] },
        ),
      ]);

      if (!alive) {
        return;
      }

      setOrganizations(Array.isArray(orgPayload.organizations) ? orgPayload.organizations : []);
      setJobs(Array.isArray(jobsPayload.jobs) ? jobsPayload.jobs : []);
      setSelectedTasks([]);
      setOpenMenuId(null);
      setLoading(false);
    }

    void load();
    return () => {
      alive = false;
    };
  }, [id, includeSub]);

  const currentOrg = useMemo(
    () => organizations.find((org) => org.id === id) ?? null,
    [id, organizations],
  );

  useEffect(() => {
    if (jobs.some((job) => job.report_kind === "budget")) {
      setActiveTab("budget");
      return;
    }
    if (jobs.some((job) => job.report_kind === "final")) {
      setActiveTab("final");
    }
  }, [jobs]);

  const years = useMemo(() => {
    const uniqueYears = new Set<number>();
    for (const job of jobs) {
      if (typeof job.report_year === "number" && job.report_year > 0) {
        uniqueYears.add(job.report_year);
      }
    }
    return Array.from(uniqueYears).sort((left, right) => right - left);
  }, [jobs]);

  const filteredTasks = useMemo(() => {
    return jobs.filter((job) => {
      if (activeTab === "budget" && job.report_kind !== "budget") {
        return false;
      }
      if (activeTab === "final" && job.report_kind !== "final") {
        return false;
      }
      if (selectedYear !== "all" && String(job.report_year ?? "") !== selectedYear) {
        return false;
      }
      if (selectedStatus !== "all" && normalizeUiTaskStatus(job.status) !== selectedStatus) {
        return false;
      }
      if (normalizedSearchQuery) {
        const task = toUiTask(job);
        const status = normalizeUiTaskStatus(job.status);
        const searchText = normalizeSearchValue(
          [
            task.filename,
            task.department,
            job.filename,
            job.organization_name,
            job.doc_type,
            job.report_year,
            getSearchStatusLabel(status),
            job.job_id,
          ].join(" "),
        );

        if (!searchText.includes(normalizedSearchQuery)) {
          return false;
        }
      }
      return true;
    });
  }, [activeTab, jobs, normalizedSearchQuery, selectedStatus, selectedYear]);

  useEffect(() => {
    const visibleTaskIds = new Set(filteredTasks.map((task) => task.job_id));

    setSelectedTasks((current) => {
      const next = current.filter((taskId) => visibleTaskIds.has(taskId));
      return next.length === current.length ? current : next;
    });

    if (openMenuId && !visibleTaskIds.has(openMenuId)) {
      setOpenMenuId(null);
    }
  }, [filteredTasks, openMenuId]);

  const pendingCount = useMemo(
    () =>
      jobs.filter(
        (job) =>
          Number(job.review_item_count ?? 0) > 0 ||
          normalizeUiTaskStatus(job.status) === "analyzing",
      ).length,
    [jobs],
  );

  const toggleSelect = (taskId: string) => {
    setSelectedTasks((prev) =>
      prev.includes(taskId) ? prev.filter((item) => item !== taskId) : [...prev, taskId],
    );
  };

  const toggleAll = () => {
    if (selectedTasks.length === filteredTasks.length) {
      setSelectedTasks([]);
      return;
    }
    setSelectedTasks(filteredTasks.map((task) => task.job_id));
  };

  const includeChildrenDisabled = currentOrg?.level === "unit";

  return (
    <div className="flex h-full flex-col bg-surface-50">
      <div className="shrink-0 border-b border-border bg-white px-8 pt-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">
              {currentOrg?.name ?? "组织详情"}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {loading
                ? "正在同步真实任务数据..."
                : `共 ${jobs.length} 份报告，其中 ${pendingCount} 份待复核`}
            </p>
          </div>
          <div className="flex items-center gap-4">
            <label
              className={cn(
                "flex items-center gap-2",
                includeChildrenDisabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
              )}
            >
              <div className="relative">
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={includeSub}
                  disabled={includeChildrenDisabled}
                  onChange={() => setIncludeSub((prev) => !prev)}
                />
                <div
                  className={cn(
                    "block h-6 w-10 rounded-full transition-colors",
                    includeSub ? "bg-primary-600" : "bg-slate-300",
                  )}
                />
                <div
                  className={cn(
                    "dot absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition-transform",
                    includeSub && "translate-x-4",
                  )}
                />
              </div>
              <span className="text-sm font-medium text-slate-700">包含下属单位</span>
            </label>
            <div className="mx-2 h-6 w-px bg-border" />
            <button className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 font-medium text-white shadow-sm transition-colors hover:bg-primary-700">
              <UploadCloud className="h-4 w-4" />
              上传报告
            </button>
          </div>
        </div>

        <div className="flex items-center gap-6">
          <button
            onClick={() => {
              setActiveTab("budget");
              setSelectedTasks([]);
            }}
            className={cn(
              "border-b-2 pb-3 text-sm font-medium transition-colors",
              activeTab === "budget"
                ? "border-primary-600 text-primary-600"
                : "border-transparent text-slate-500 hover:text-slate-700",
            )}
          >
            部门预算
          </button>
          <button
            onClick={() => {
              setActiveTab("final");
              setSelectedTasks([]);
            }}
            className={cn(
              "border-b-2 pb-3 text-sm font-medium transition-colors",
              activeTab === "final"
                ? "border-primary-600 text-primary-600"
                : "border-transparent text-slate-500 hover:text-slate-700",
            )}
          >
            部门决算
          </button>
        </div>
      </div>

      <div className="relative flex-1 overflow-y-auto p-8">
        {selectedTasks.length > 0 && (
          <div className="animate-in fade-in slide-in-from-top-4 absolute left-1/2 top-4 z-20 flex -translate-x-1/2 items-center gap-6 rounded-xl bg-slate-900 px-6 py-3 text-white shadow-xl">
            <span className="text-sm font-medium">已选择 {selectedTasks.length} 项</span>
            <div className="h-4 w-px bg-slate-700" />
            <div className="flex items-center gap-2">
              <button className="flex items-center gap-2 rounded px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-800">
                <RefreshCw className="h-4 w-4" />
                批量重分析
              </button>
              <button className="flex items-center gap-2 rounded px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-800">
                <Download className="h-4 w-4" />
                批量导出
              </button>
            </div>
          </div>
        )}

        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <select
              value={selectedYear}
              onChange={(event) => setSelectedYear(event.target.value)}
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="all">全部年度</option>
              {years.map((year) => (
                <option key={year} value={String(year)}>
                  {year}
                </option>
              ))}
            </select>
            <select
              value={selectedStatus}
              onChange={(event) => setSelectedStatus(event.target.value)}
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="all">全部状态</option>
              <option value="completed">已完成</option>
              <option value="analyzing">分析中</option>
              <option value="failed">失败</option>
            </select>
          </div>
          <button className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50">
            <Filter className="h-4 w-4" />
            高级筛选
          </button>
        </div>

        <div className="overflow-visible rounded-xl border border-border bg-white shadow-sm">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-border bg-slate-50 text-sm font-semibold text-slate-600">
                <th className="w-12 p-4 text-center">
                  <input
                    type="checkbox"
                    className="rounded border-slate-300 text-primary-600 focus:ring-primary-500"
                    checked={selectedTasks.length === filteredTasks.length && filteredTasks.length > 0}
                    onChange={toggleAll}
                  />
                </th>
                <th className="p-4">文件名</th>
                <th className="w-24 p-4">年度</th>
                <th className="w-32 p-4">状态</th>
                <th className="w-32 p-4">问题数</th>
                <th className="w-48 p-4">更新时间</th>
                <th className="w-24 p-4 text-center">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-500">
                    正在加载任务...
                  </td>
                </tr>
              ) : filteredTasks.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-8 text-center text-slate-500">
                    {normalizedSearchQuery
                      ? "当前筛选条件与搜索关键词下暂无相关报告"
                      : "当前筛选条件下暂无相关报告"}
                  </td>
                </tr>
              ) : (
                filteredTasks.map((job) => {
                  const task = toUiTask(job);
                  return (
                    <tr
                      key={job.job_id}
                      className={cn(
                        "transition-colors hover:bg-slate-50",
                        selectedTasks.includes(job.job_id) && "bg-primary-50/50 hover:bg-primary-50/80",
                      )}
                    >
                      <td className="p-4 text-center">
                        <input
                          type="checkbox"
                          className="rounded border-slate-300 text-primary-600 focus:ring-primary-500"
                          checked={selectedTasks.includes(job.job_id)}
                          onChange={() => toggleSelect(job.job_id)}
                        />
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-3">
                          <Link
                            href={`/task/${job.job_id}` as Route}
                            className="font-medium text-slate-900 hover:text-primary-600"
                          >
                            {task.filename}
                          </Link>
                          <div className="flex items-center gap-1">
                            <span className="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-xs font-bold text-blue-600">
                              V{task.version}
                            </span>
                            {task.version > 1 && (
                              <button className="text-slate-400 transition-colors hover:text-primary-600" title="查看历史版本">
                                <History className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="p-4 text-sm text-slate-600">{task.year}</td>
                      <td className="p-4">
                        <span
                          className={cn(
                            "rounded-full border px-2.5 py-1 text-xs font-medium",
                            task.status === "completed"
                              ? "border-success-200 bg-success-50 text-success-700"
                              : task.status === "analyzing"
                                ? "border-warning-200 bg-warning-50 text-warning-700"
                                : "border-danger-200 bg-danger-50 text-danger-700",
                          )}
                        >
                          {task.status === "completed"
                            ? "已完成"
                            : task.status === "analyzing"
                              ? "分析中"
                              : "失败"}
                        </span>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-900">{getDisplayIssueTotal(job)}</span>
                          {getHighRiskCount(job) > 0 && (
                            <span className="rounded bg-danger-100 px-1.5 py-0.5 text-xs font-bold text-danger-700">
                              {getHighRiskCount(job)} 高危
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="p-4 text-sm text-slate-500">
                        {formatDateTime(job.updated_ts ?? job.ts)}
                      </td>
                      <td className="relative p-4 text-center">
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            setOpenMenuId(openMenuId === job.job_id ? null : job.job_id);
                          }}
                          className="rounded p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700"
                        >
                          <MoreHorizontal className="h-5 w-5" />
                        </button>

                        {openMenuId === job.job_id && (
                          <div
                            ref={menuRef}
                            className="animate-in fade-in zoom-in-95 absolute right-8 top-10 z-30 w-40 rounded-lg border border-border bg-white py-1 shadow-lg"
                          >
                            <button
                              onClick={() => router.push(`/task/${job.job_id}` as Route)}
                              className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"
                            >
                              <Eye className="h-4 w-4 text-slate-400" />
                              查看复核详情
                            </button>
                            <button className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-400">
                              <RefreshCw className="h-4 w-4" />
                              重新分析
                            </button>
                            <button className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-400">
                              <History className="h-4 w-4" />
                              历史版本记录
                            </button>
                            <button className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-400">
                              <Download className="h-4 w-4" />
                              导出审查报告
                            </button>
                            <div className="my-1 h-px bg-border" />
                            <button className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-400">
                              <Trash2 className="h-4 w-4" />
                              删除此报告
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
