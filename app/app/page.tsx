"use client";

import type { Route } from "next";
import Link from "next/link";
import { ArrowUpRight, FileText, AlertTriangle, CheckCircle2, UploadCloud } from "lucide-react";
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

function isSameCalendarDay(timestamp: unknown) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) {
    return false;
  }
  const millis = value > 1_000_000_000_000 ? value : value * 1000;
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

  const summary = useMemo(() => {
    const totalReports = jobs.length;
    const totalHighRisk = jobs.reduce((sum, job) => sum + getHighRiskCount(job), 0);
    const completedToday = jobs.filter(
      (job) => job.status === "done" || job.status === "completed",
    ).filter((job) => isSameCalendarDay(job.updated_ts ?? job.ts)).length;
    const pendingReview = jobs.filter(
      (job) => Number(job.review_item_count ?? 0) > 0 || !(job.status === "done" || job.status === "completed"),
    ).length;

    return {
      totalReports,
      totalHighRisk,
      completedToday,
      pendingReview,
    };
  }, [jobs]);

  const highlightedDepartments = useMemo(
    () =>
      [...departments]
        .sort(
          (left, right) =>
            Number(right.issue_count ?? 0) - Number(left.issue_count ?? 0) ||
            left.name.localeCompare(right.name, "zh-CN"),
        )
        .slice(0, 5),
    [departments],
  );

  const recentJobs = useMemo(() => jobs.slice(0, 8), [jobs]);
  const primaryDepartmentHref =
    jobs.find((item) => item.organization_id)?.organization_id ??
    departments[0]?.id ??
    null;

  return (
    <>
      <div className="mx-auto max-w-7xl p-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">总览</h1>
          <p className="mt-1 text-slate-500">
            {loading
              ? "正在加载历史数据..."
              : `当前已接入 ${summary.totalReports} 份历史报告，其中 ${summary.pendingReview} 份仍需重点关注。`}
          </p>
        </div>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => setIsUploadModalOpen(true)}
            className="flex items-center gap-2 rounded-lg border border-border bg-white px-4 py-2 font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
          >
            <UploadCloud className="h-4 w-4" />
            上传报告
          </button>
          <Link
            href={(primaryDepartmentHref ? `/department/${primaryDepartmentHref}` : "/") as Route}
            className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 font-medium text-white shadow-sm transition-colors hover:bg-primary-700"
          >
            进入复核
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </div>
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="flex items-center gap-4 rounded-xl border border-border bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-50 text-primary-600">
            <FileText className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">累计审查报告</p>
            <p className="mt-1 text-3xl font-bold text-slate-900">{summary.totalReports}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-xl border border-border bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-danger-50 text-danger-600">
            <AlertTriangle className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">高风险问题</p>
            <p className="mt-1 text-3xl font-bold text-slate-900">{summary.totalHighRisk}</p>
          </div>
        </div>
        <div className="flex items-center gap-4 rounded-xl border border-border bg-white p-6 shadow-sm">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-success-50 text-success-600">
            <CheckCircle2 className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">今日完成数</p>
            <p className="mt-1 text-3xl font-bold text-slate-900">{summary.completedToday}</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <div className="overflow-hidden rounded-xl border border-border bg-white shadow-sm">
            <div className="flex items-center justify-between border-b border-border bg-slate-50/50 px-6 py-4">
              <h2 className="text-base font-semibold text-slate-900">最近任务</h2>
              <span className="text-sm text-slate-500">
                最近更新时间：{recentJobs[0] ? formatDateTime(recentJobs[0].updated_ts ?? recentJobs[0].ts) : "--"}
              </span>
            </div>
            <div className="divide-y divide-border">
              {loading ? (
                <div className="p-6 text-sm text-slate-500">正在同步真实任务数据...</div>
              ) : recentJobs.length === 0 ? (
                <div className="p-6 text-sm text-slate-500">当前仓库中还没有可展示的任务。</div>
              ) : (
                recentJobs.map((job) => {
                  const task = toUiTask(job);
                  return (
                    <div
                      key={job.job_id}
                      className="flex items-center justify-between p-6 transition-colors hover:bg-slate-50"
                    >
                      <div className="flex items-start gap-4">
                        <div className="mt-1 flex h-10 w-10 shrink-0 items-center justify-center rounded bg-slate-100 text-slate-500">
                          <FileText className="h-5 w-5" />
                        </div>
                        <div>
                          <Link
                            href={`/task/${job.job_id}` as Route}
                            className="text-base font-medium text-slate-900 transition-colors hover:text-primary-600"
                          >
                            {task.filename}
                          </Link>
                          <div className="mt-2 flex items-center gap-3 text-sm text-slate-500">
                            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                              {task.year}
                            </span>
                            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                              {task.department}
                            </span>
                            <span>{task.updatedAt}</span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-6">
                        <div className="text-right">
                          <p className="text-sm text-slate-500">问题数</p>
                          <p className="text-lg font-semibold text-slate-900">
                            {getDisplayIssueTotal(job)}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm text-slate-500">高风险</p>
                          <p className="text-lg font-semibold text-danger-600">
                            {getHighRiskCount(job)}
                          </p>
                        </div>
                        <Link
                          href={`/task/${job.job_id}` as Route}
                          className="rounded-md bg-primary-50 px-3 py-1.5 text-sm font-medium text-primary-700 transition-colors hover:bg-primary-100"
                        >
                          查看详情
                        </Link>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        <div className="space-y-6">
          <div className="overflow-hidden rounded-xl border border-border bg-white shadow-sm">
            <div className="border-b border-border bg-slate-50/50 px-6 py-4">
              <h2 className="text-base font-semibold text-slate-900">重点异常部门</h2>
            </div>
            <div className="space-y-4 p-6">
              {loading ? (
                <div className="text-sm text-slate-500">正在加载部门统计...</div>
              ) : highlightedDepartments.length === 0 ? (
                <div className="text-sm text-slate-500">暂无部门异常统计。</div>
              ) : (
                highlightedDepartments.map((department, index) => (
                  <div key={department.id} className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-danger-50 text-xs font-bold text-danger-600">
                        {index + 1}
                      </div>
                      <Link
                        href={`/department/${department.id}` as Route}
                        className="text-sm font-medium text-slate-700 transition-colors hover:text-primary-600"
                      >
                        {department.name}
                      </Link>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-semibold text-slate-900">
                        {department.issue_count ?? 0} 项
                      </span>
                      <span className="text-xs font-medium text-slate-500">
                        {department.job_count ?? 0} 份文件
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
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
