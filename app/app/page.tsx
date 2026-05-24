"use client";

import type { Route } from "next";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ClipboardList,
  FileText,
  UploadCloud,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import BatchUploadModal from "@/components/BatchUploadModal";
import type { JobSummaryRecord, OrganizationRecord } from "@/lib/uiAdapters";
import {
  formatDateTime,
  getDisplayIssueTotal,
  getHighRiskCount,
  toUiTask,
} from "@/lib/uiAdapters";

type DepartmentsResponse = {
  departments?: OrganizationRecord[];
};

type JobsResponse =
  | JobSummaryRecord[]
  | {
      items?: JobSummaryRecord[];
    };

function SummaryCard({
  label,
  value,
  description,
  tone,
  icon,
}: {
  label: string;
  value: number;
  description: string;
  tone: "primary" | "danger" | "success" | "slate";
  icon: ReactNode;
}) {
  const toneClass =
    tone === "danger"
      ? "bg-red-50 text-red-600"
      : tone === "success"
        ? "bg-emerald-50 text-emerald-600"
        : tone === "slate"
          ? "bg-slate-100 text-slate-600"
          : "bg-primary-50 text-primary-600";

  return (
    <div className="rounded-2xl border border-border bg-white p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-3xl font-bold tracking-tight text-slate-900">{value}</p>
          <p className="mt-2 text-sm text-slate-500">{description}</p>
        </div>
        <div className={`flex h-12 w-12 items-center justify-center rounded-2xl ${toneClass}`}>
          {icon}
        </div>
      </div>
    </div>
  );
}

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

function normalizeTimestamp(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0;
  }
  return numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
}

function isSameCalendarDay(timestamp: unknown) {
  const millis = normalizeTimestamp(timestamp);
  if (!millis) {
    return false;
  }

  const target = new Date(millis);
  const now = new Date();
  return (
    target.getFullYear() === now.getFullYear() &&
    target.getMonth() === now.getMonth() &&
    target.getDate() === now.getDate()
  );
}

export default function Dashboard() {
  const [jobs, setJobs] = useState<JobSummaryRecord[]>([]);
  const [departments, setDepartments] = useState<OrganizationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshSeed, setRefreshSeed] = useState(0);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);

      const [jobsPayload, departmentsPayload] = await Promise.all([
        fetchJson<JobsResponse>("/api/jobs", []),
        fetchJson<DepartmentsResponse>("/api/departments", { departments: [] }),
      ]);

      if (!alive) {
        return;
      }

      const nextJobs = Array.isArray(jobsPayload)
        ? jobsPayload
        : Array.isArray(jobsPayload.items)
          ? jobsPayload.items
          : [];
      const nextDepartments = Array.isArray(departmentsPayload.departments)
        ? departmentsPayload.departments
        : [];

      setJobs(nextJobs);
      setDepartments(nextDepartments);
      setLoading(false);
    }

    void load();
    return () => {
      alive = false;
    };
  }, [refreshSeed]);

  const sortedJobs = useMemo(
    () =>
      [...jobs].sort(
        (left, right) =>
          normalizeTimestamp(right.updated_ts ?? right.ts ?? right.created_ts) -
          normalizeTimestamp(left.updated_ts ?? left.ts ?? left.created_ts),
      ),
    [jobs],
  );

  const summary = useMemo(() => {
    const totalReports = sortedJobs.length;
    const totalHighRisk = sortedJobs.reduce((sum, job) => sum + getHighRiskCount(job), 0);
    const completedToday = sortedJobs
      .filter((job) => job.status === "done" || job.status === "completed")
      .filter((job) => isSameCalendarDay(job.updated_ts ?? job.ts ?? job.created_ts)).length;
    const pendingReview = sortedJobs.filter(
      (job) =>
        Number(job.review_item_count ?? 0) > 0 ||
        !["done", "completed"].includes(String(job.status ?? "").toLowerCase()),
    ).length;

    return {
      totalReports,
      totalHighRisk,
      completedToday,
      pendingReview,
    };
  }, [sortedJobs]);

  const highlightedDepartments = useMemo(
    () =>
      [...departments]
        .sort(
          (left, right) =>
            Number(right.issue_count ?? 0) - Number(left.issue_count ?? 0) ||
            Number(right.job_count ?? 0) - Number(left.job_count ?? 0) ||
            left.name.localeCompare(right.name, "zh-CN"),
        )
        .slice(0, 6),
    [departments],
  );

  const recentJobs = useMemo(() => sortedJobs.slice(0, 8), [sortedJobs]);
  const reviewHref = (
    sortedJobs.find((item) => item.organization_id)?.organization_id
      ? `/department/${sortedJobs.find((item) => item.organization_id)?.organization_id}`
      : departments[0]?.id
        ? `/department/${departments[0].id}`
        : "/admin?tab=operations"
  ) as Route;

  return (
    <>
      <div className="mx-auto max-w-7xl p-8">
        <section className="rounded-[28px] border border-border bg-[radial-gradient(circle_at_top_left,_rgba(37,99,235,0.14),_transparent_35%),linear-gradient(135deg,#ffffff,#f8fafc)] p-8 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div className="max-w-3xl">
              <div className="inline-flex rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold tracking-[0.18em] text-primary-700">
                审校工作台
              </div>
              <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900">报告总览</h1>
              <p className="mt-3 text-sm leading-6 text-slate-600">
                {loading
                  ? "正在加载报告和组织结构数据。"
                  : `当前共接入 ${summary.totalReports} 份报告，其中 ${summary.pendingReview} 份仍需重点复核。`}
              </p>
            </div>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setIsUploadModalOpen(true)}
                className="inline-flex items-center gap-2 rounded-xl border border-border bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
              >
                <UploadCloud className="h-4 w-4" />
                批量上传报告
              </button>
              <Link
                href={reviewHref}
                className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-700"
              >
                进入复核
                <ArrowUpRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </section>

        <section className="mt-8 grid gap-6 md:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            label="累计接入报告"
            value={summary.totalReports}
            description="已进入系统并可继续分析或复核"
            tone="primary"
            icon={<FileText className="h-6 w-6" />}
          />
          <SummaryCard
            label="高风险问题"
            value={summary.totalHighRisk}
            description="来自已识别的高优先级问题项"
            tone="danger"
            icon={<AlertTriangle className="h-6 w-6" />}
          />
          <SummaryCard
            label="今日完成"
            value={summary.completedToday}
            description="今天完成分析的报告数量"
            tone="success"
            icon={<CheckCircle2 className="h-6 w-6" />}
          />
          <SummaryCard
            label="待复核任务"
            value={summary.pendingReview}
            description="包含未完成分析或仍有复核项"
            tone="slate"
            icon={<ClipboardList className="h-6 w-6" />}
          />
        </section>

        <div className="mt-8 grid gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
          <section className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-border bg-slate-50/70 px-6 py-4">
              <div>
                <h2 className="text-base font-semibold text-slate-900">最近任务</h2>
                <p className="mt-1 text-sm text-slate-500">
                  最近更新时间：
                  {recentJobs[0]
                    ? formatDateTime(recentJobs[0].updated_ts ?? recentJobs[0].ts ?? recentJobs[0].created_ts)
                    : "--"}
                </p>
              </div>
            </div>

            <div className="divide-y divide-border">
              {loading ? (
                <div className="p-6 text-sm text-slate-500">正在同步最新任务数据...</div>
              ) : recentJobs.length === 0 ? (
                <div className="p-6 text-sm text-slate-500">当前还没有可展示的报告任务。</div>
              ) : (
                recentJobs.map((job) => {
                  const task = toUiTask(job);
                  return (
                    <div
                      key={job.job_id}
                      className="flex flex-wrap items-center justify-between gap-4 p-6 transition-colors hover:bg-slate-50"
                    >
                      <div className="min-w-0 flex-1">
                        <Link
                          href={`/task/${job.job_id}` as Route}
                          className="block truncate text-base font-semibold text-slate-900 transition-colors hover:text-primary-600"
                        >
                          {task.filename}
                        </Link>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                          <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                            {task.year}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                            {task.department}
                          </span>
                          <span>{task.updatedAt}</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-6">
                        <div className="text-right">
                          <div className="text-xs text-slate-500">问题总数</div>
                          <div className="mt-1 text-lg font-semibold text-slate-900">
                            {getDisplayIssueTotal(job)}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-xs text-slate-500">高风险</div>
                          <div className="mt-1 text-lg font-semibold text-red-600">
                            {getHighRiskCount(job)}
                          </div>
                        </div>
                        <Link
                          href={`/task/${job.job_id}` as Route}
                          className="rounded-lg bg-primary-50 px-3 py-2 text-sm font-medium text-primary-700 transition-colors hover:bg-primary-100"
                        >
                          查看详情
                        </Link>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </section>

          <section className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
            <div className="border-b border-border bg-slate-50/70 px-6 py-4">
              <h2 className="text-base font-semibold text-slate-900">重点异常部门</h2>
              <p className="mt-1 text-sm text-slate-500">按问题数和任务量排序展示。</p>
            </div>

            <div className="space-y-4 p-6">
              {loading ? (
                <div className="text-sm text-slate-500">正在加载部门统计...</div>
              ) : highlightedDepartments.length === 0 ? (
                <div className="text-sm text-slate-500">当前没有需要重点关注的部门统计。</div>
              ) : (
                highlightedDepartments.map((department, index) => (
                  <div key={department.id} className="flex items-center justify-between gap-4">
                    <div className="min-w-0 flex items-center gap-3">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-red-50 text-xs font-bold text-red-600">
                        {index + 1}
                      </div>
                      <Link
                        href={`/department/${department.id}` as Route}
                        className="truncate text-sm font-medium text-slate-700 transition-colors hover:text-primary-600"
                      >
                        {department.name}
                      </Link>
                    </div>

                    <div className="shrink-0 text-right">
                      <div className="text-sm font-semibold text-slate-900">
                        {department.issue_count ?? 0} 项问题
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {department.job_count ?? 0} 份报告
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>

      {isUploadModalOpen ? (
        <BatchUploadModal
          defaultDocType="dept_budget"
          onClose={() => setIsUploadModalOpen(false)}
          onComplete={() => {
            setIsUploadModalOpen(false);
            setRefreshSeed((value) => value + 1);
          }}
        />
      ) : null}
    </>
  );
}
