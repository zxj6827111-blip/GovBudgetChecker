"use client";

import type { Route } from "next";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Download, RefreshCw, FileText } from "lucide-react";

import EvidencePanel from "@/components/task-review/EvidencePanel";
import PDFHighlighter from "@/components/task-review/PDFHighlighter";
import PipelineDrawer from "@/components/task-review/PipelineDrawer";
import ProblemSidebar from "@/components/task-review/ProblemSidebar";
import ReportPreviewModal from "@/components/task-review/ReportPreviewModal";
import type { Problem, Task } from "@/lib/mock";
import { cn } from "@/lib/utils";
import type { JobDetailRecord, StructuredIngestRecord } from "@/lib/uiAdapters";
import { toUiProblems, toUiTask } from "@/lib/uiAdapters";
import { buildProblemPreviewUrl, normalizeProblemBbox } from "@/components/task-review/problemPreview";

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

export default function TaskDetail() {
  const { job_id } = useParams<{ job_id: string }>();
  const router = useRouter();
  const [task, setTask] = useState<Task | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedProblemId, setSelectedProblemId] = useState("");
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isViewerOpen, setIsViewerOpen] = useState(false);
  const [isReportModalOpen, setIsReportModalOpen] = useState(false);

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);
      const [detail, structured] = await Promise.all([
        fetchJson<JobDetailRecord | null>(`/api/jobs/${encodeURIComponent(job_id)}`, null),
        fetchJson<StructuredIngestRecord>(`/api/jobs/${encodeURIComponent(job_id)}/structured-ingest`, {}),
      ]);

      if (!alive || !detail) {
        if (alive) {
          setTask(null);
          setProblems([]);
          setLoading(false);
        }
        return;
      }

      const nextTask = toUiTask(detail, structured);
      const nextProblems = toUiProblems({
        ...detail,
        structured_ingest: structured,
      }).map((problem) => ({
        ...problem,
        jobId: detail.job_id,
        bbox: normalizeProblemBbox(problem.bbox),
        evidenceImage:
          buildProblemPreviewUrl(detail.job_id, problem.page) ?? problem.evidenceImage,
      }));

      setTask(nextTask);
      setProblems(nextProblems);
      setSelectedProblemId((current) => {
        if (nextProblems.some((problem) => problem.id === current)) {
          return current;
        }
        return nextProblems[0]?.id ?? "";
      });
      setLoading(false);
    }

    void load();
    return () => {
      alive = false;
    };
  }, [job_id]);

  const selectedProblem = useMemo(
    () => problems.find((problem) => problem.id === selectedProblemId),
    [problems, selectedProblemId],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-50">
        <div className="text-sm text-slate-500">正在加载任务详情...</div>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-50">
        <div className="text-sm text-slate-500">未找到对应任务。</div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-surface-50">
      <div className="z-10 flex h-16 shrink-0 items-center justify-between border-b border-border bg-white px-6 shadow-sm">
        <div className="flex items-center gap-4">
          <Link
            href={(task.departmentId ? `/department/${task.departmentId}` : "/") as Route}
            onClick={(event) => {
              event.preventDefault();

              if (window.history.length > 1) {
                router.back();
                return;
              }

              router.push((task.departmentId ? `/department/${task.departmentId}` : "/") as Route);
            }}
            className="rounded-md p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div className="h-6 w-px bg-border" />
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded bg-primary-50 text-primary-600">
              <FileText className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight text-slate-900">{task.filename}</h1>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                <span>{task.department}</span>
                <span className="h-1 w-1 rounded-full bg-slate-300" />
                <span className="hidden">
                  {task.year} {task.type === "budget" ? "部门预算" : "部门决算"}
                </span>
                <span>{task.year} {task.reportLabel}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
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
              ? "分析完成"
              : task.status === "analyzing"
                ? "分析中"
                : "分析失败"}
          </span>
          <div className="mx-2 h-6 w-px bg-border" />
          <button className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50">
            <RefreshCw className="h-4 w-4 text-slate-400" />
            重新分析
          </button>
          <button
            onClick={() => setIsReportModalOpen(true)}
            className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
          >
            <Download className="h-4 w-4 text-slate-400" />
            导出报告
          </button>
        </div>
      </div>

      <div className="relative flex flex-1 overflow-hidden">
        <PipelineDrawer
          isOpen={isDrawerOpen}
          onToggle={() => setIsDrawerOpen((prev) => !prev)}
          task={task}
        />

        <ProblemSidebar
          problems={problems}
          selectedId={selectedProblemId}
          onSelect={setSelectedProblemId}
        />

        <div className="flex-1 overflow-y-auto bg-slate-50/50 p-6">
          {selectedProblem ? (
            <EvidencePanel
              problem={selectedProblem}
              onOpenViewer={() => setIsViewerOpen(true)}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-slate-400">
              <FileText className="mb-4 h-12 w-12 text-slate-300" />
              <p className="text-sm font-medium">
                {problems.length === 0 ? "当前任务暂无问题。" : "请在左侧选择一个问题以查看证据"}
              </p>
            </div>
          )}
        </div>
      </div>

      {isViewerOpen && selectedProblem && (
        <PDFHighlighter
          problem={selectedProblem}
          onClose={() => setIsViewerOpen(false)}
        />
      )}

      {isReportModalOpen && (
        <ReportPreviewModal
          task={task}
          problems={problems}
          onClose={() => setIsReportModalOpen(false)}
        />
      )}
    </div>
  );
}
