"use client";

import type { Route } from "next";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Download, RefreshCw, FileText, Link2 } from "lucide-react";

import AssociateDialog from "@/components/AssociateDialog";
import { useAIAnalysisOptions } from "@/components/AIAnalysisOptions";
import AnalysisCheckProgress from "@/components/AnalysisCheckProgress";
import ReanalyzeAiToggle from "@/components/ReanalyzeAiToggle";
import EvidencePanel from "@/components/task-review/EvidencePanel";
import PDFHighlighter from "@/components/task-review/PDFHighlighter";
import PipelineDrawer from "@/components/task-review/PipelineDrawer";
import ProblemSidebar from "@/components/task-review/ProblemSidebar";
import ReportPreviewModal from "@/components/task-review/ReportPreviewModal";
import { isHighRiskSeverity } from "@/lib/issueSeverity";
import type { Problem, Task } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { recognitionStateLabel } from "@/lib/reportVersion";
import type { JobDetailRecord, StructuredIngestRecord } from "@/lib/uiAdapters";
import { selectStructuredForJob, toUiProblems, toUiTask } from "@/lib/uiAdapters";
import {
  buildProblemPreviewUrl,
  normalizeProblemBbox,
} from "@/components/task-review/problemPreview";

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

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload?.detail || payload?.error || payload?.message || text || `HTTP ${response.status}`;
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function normalizeProblemSearchValue(value: string): string {
  return value.trim().toLowerCase();
}

export default function TaskDetail() {
  const { job_id } = useParams<{ job_id: string }>();
  const router = useRouter();
  const aiOptions = useAIAnalysisOptions();
  const [task, setTask] = useState<Task | null>(null);
  const [jobDetail, setJobDetail] = useState<JobDetailRecord | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedProblemId, setSelectedProblemId] = useState("");
  const [selectedEvidenceRef, setSelectedEvidenceRef] = useState<{ problemId: string; refKey: string } | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isViewerOpen, setIsViewerOpen] = useState(false);
  const [isReportModalOpen, setIsReportModalOpen] = useState(false);
  const [isReanalyzing, setIsReanalyzing] = useState(false);
  const [isAssociateDialogOpen, setIsAssociateDialogOpen] = useState(false);
  const [isAssociating, setIsAssociating] = useState(false);
  const [reanalyzeUseAiAssist, setReanalyzeUseAiAssist] = useState<boolean | null>(null);
  const [refreshSeed, setRefreshSeed] = useState(0);
  const [problemSearchValue, setProblemSearchValue] = useState("");
  const [problemCategory, setProblemCategory] = useState("全部");
  const [highRiskOnly, setHighRiskOnly] = useState(false);
  const [ignoringProblemId, setIgnoringProblemId] = useState<string | null>(null);
  const [selectedRevision, setSelectedRevision] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("analysis_revision") ?? "");
  const [revisions, setRevisions] = useState<Array<{revision_id: string; is_current: boolean; state: string}>>([]);

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);
      const revisionQuery = selectedRevision ? `?analysis_revision=${encodeURIComponent(selectedRevision)}` : "";
      const [detail, structured, versions] = await Promise.all([
        fetchJson<JobDetailRecord | null>(`/api/jobs/${encodeURIComponent(job_id)}${revisionQuery}`, null),
        selectedRevision ? Promise.resolve({} as StructuredIngestRecord) : fetchJson<StructuredIngestRecord>(
          `/api/jobs/${encodeURIComponent(job_id)}/structured-ingest`,
          {}
        ),
        fetchJson<{items: typeof revisions}>(`/api/jobs/${encodeURIComponent(job_id)}/analysis-versions`, {items: []}),
      ]);

      if (!alive || !detail) {
        if (alive) {
          setJobDetail(null);
          setTask(null);
          setProblems([]);
          setLoading(false);
        }
        return;
      }

      const boundStructured = selectStructuredForJob(detail, structured);
      const nextTask = toUiTask(detail, boundStructured);
      if (selectedRevision) nextTask.analysisRevision = selectedRevision;
      const nextProblems = toUiProblems({
        ...detail,
        structured_ingest: boundStructured,
      }).map((problem) => ({
        ...problem,
        jobId: detail.job_id,
        bbox: normalizeProblemBbox(problem.bbox),
        evidenceImage: buildProblemPreviewUrl(detail.job_id, problem.page) ?? problem.evidenceImage,
      }));

      setJobDetail(detail);
      setRevisions(versions.items);
      setTask(nextTask);
      setProblems(nextProblems);
      setLoading(false);
    }

    void load();
    return () => {
      alive = false;
    };
  }, [job_id, refreshSeed, selectedRevision]);

  const problemCategories = useMemo(() => {
    const categories = new Set<string>();
    problems.forEach((problem) => {
      if (problem.category) {
        categories.add(problem.category);
      }
    });
    return ["全部", ...Array.from(categories)];
  }, [problems]);

  useEffect(() => {
    if (!problemCategories.includes(problemCategory)) {
      setProblemCategory("全部");
    }
  }, [problemCategories, problemCategory]);

  const filteredProblems = useMemo(() => {
    const normalizedKeyword = normalizeProblemSearchValue(problemSearchValue);

    return problems.filter((problem) => {
      if (problemCategory !== "全部" && problem.category !== problemCategory) {
        return false;
      }
      if (highRiskOnly && !isHighRiskSeverity(problem.severity)) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }

      const haystack = normalizeProblemSearchValue(
        [problem.ruleId, problem.title, problem.description, problem.category, problem.location].join(" ")
      );
      return haystack.includes(normalizedKeyword);
    });
  }, [highRiskOnly, problemCategory, problemSearchValue, problems]);

  useEffect(() => {
    setSelectedProblemId((current) => {
      if (filteredProblems.some((problem) => problem.id === current)) {
        return current;
      }
      return filteredProblems[0]?.id ?? "";
    });
  }, [filteredProblems]);

  const selectedProblem = useMemo(
    () => filteredProblems.find((problem) => problem.id === selectedProblemId),
    [filteredProblems, selectedProblemId]
  );
  const highlightedProblem = useMemo(() => {
    if (!selectedProblem || selectedEvidenceRef?.problemId !== selectedProblem.id) return selectedProblem;
    const ref = selectedProblem.evidenceRefs?.find((item) => item.key === selectedEvidenceRef.refKey);
    return ref ? { ...selectedProblem, page: ref.page, bbox: ref.bbox, location: ref.locationText } : selectedProblem;
  }, [selectedProblem, selectedEvidenceRef]);
  const effectiveReanalyzeUseAiAssist =
    reanalyzeUseAiAssist ?? (aiOptions.capabilities
      ? aiOptions.enabled
      : typeof jobDetail?.use_ai_assist === "boolean" ? jobDetail.use_ai_assist : false);

  const handleReanalyze = async () => {
    if (isReanalyzing || task?.status === "analyzing") {
      return;
    }

    if (
      !confirm("确定要重新分析这份报告吗？系统会基于当前任务重新执行分析。")
    ) {
      return;
    }

    setIsReanalyzing(true);

    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(job_id)}/reanalyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          use_local_rules: true,
          use_ai_assist: effectiveReanalyzeUseAiAssist,
          ai_profile_id: effectiveReanalyzeUseAiAssist ? aiOptions.profileId || null : null,
        }),
      });

      if (!response.ok) {
        alert(await readErrorMessage(response));
        return;
      }

      setTask((current) => (current ? { ...current, status: "analyzing" } : current));
      setJobDetail((current) =>
        current
          ? {
              ...current,
              use_local_rules: true,
              use_ai_assist: effectiveReanalyzeUseAiAssist,
            }
          : current
      );
      setRefreshSeed((current) => current + 1);
      alert("已开始重新分析，页面会自动刷新任务状态。");
    } catch (error) {
      console.error("Failed to reanalyze report:", error);
      alert("重新分析失败，请稍后重试。");
    } finally {
      setIsReanalyzing(false);
    }
  };

  const handleAssociate = async (orgId: string) => {
    setIsAssociating(true);

    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(job_id)}/associate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: orgId }),
      });

      const payload = (await response.json().catch(() => ({}))) as {
        organization_id?: string;
        organization_name?: string;
        organization_match_type?: string;
        organization_match_confidence?: number;
        detail?: string;
        error?: string;
        message?: string;
      };

      if (!response.ok) {
        throw new Error(payload.detail || payload.error || payload.message || "关联报告失败");
      }

      const nextOrganizationId = String(payload.organization_id ?? "").trim();
      const nextOrganizationName = String(payload.organization_name ?? "").trim();

      setJobDetail((current) =>
        current
          ? {
              ...current,
              organization_id: nextOrganizationId || current.organization_id,
              organization_name: nextOrganizationName || current.organization_name,
              organization_match_type:
                payload.organization_match_type || current.organization_match_type || "manual",
              organization_match_confidence:
                typeof payload.organization_match_confidence === "number"
                  ? payload.organization_match_confidence
                  : current.organization_match_confidence,
            }
          : current
      );
      setTask((current) =>
        current
          ? {
              ...current,
              department: nextOrganizationName || current.department,
              departmentId: nextOrganizationId || current.departmentId,
            }
          : current
      );
      setIsAssociateDialogOpen(false);
      setRefreshSeed((current) => current + 1);
      alert(
        nextOrganizationName
          ? `报告已关联到 ${nextOrganizationName}。`
          : "报告关联已更新。"
      );
    } catch (error) {
      console.error("Failed to associate report:", error);
      alert(error instanceof Error ? error.message : "关联报告失败，请稍后重试。");
    } finally {
      setIsAssociating(false);
    }
  };

  const handleIgnoreProblem = async () => {
    if (!selectedProblem || ignoringProblemId) {
      return;
    }

    if (!confirm("确定要忽略这个问题吗？忽略后它将不再出现在当前报告的问题列表中。")) {
      return;
    }

    const ignoredProblem = selectedProblem;
    setIgnoringProblemId(ignoredProblem.id);

    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(job_id)}/issues/ignore`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_id: ignoredProblem.id }),
        }
      );

      if (!response.ok) {
        alert(await readErrorMessage(response));
        return;
      }

      setJobDetail((current) =>
        current
          ? {
              ...current,
              ignored_issue_ids: Array.from(
                new Set([...(current.ignored_issue_ids ?? []), ignoredProblem.id])
              ),
            }
          : current
      );
      setProblems((current) => current.filter((problem) => problem.id !== ignoredProblem.id));
      setTask((current) =>
        current
          ? {
              ...current,
              problemCount: Math.max(0, current.problemCount - 1),
              highRiskCount: isHighRiskSeverity(ignoredProblem.severity)
                ? Math.max(0, current.highRiskCount - 1)
                : current.highRiskCount,
            }
          : current
      );
      alert("该问题已忽略。");
    } catch (error) {
      console.error("Failed to ignore issue:", error);
      alert("忽略问题失败，请稍后重试。");
    } finally {
      setIgnoringProblemId(null);
    }
  };

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

  const recognitionLabel = recognitionStateLabel(
    (jobDetail?.result?.meta as Record<string, unknown> | undefined)?.recognition_state
  );

  return (
    <div className="flex h-full flex-col overflow-hidden bg-surface-50" data-analysis-revision={task.analysisRevision}>
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
                <span>{task.year} {task.reportLabel}</span>
                {revisions.length > 0 && <select aria-label="分析版本" data-testid="analysis-version-selector"
                  className="max-w-44 rounded border border-border bg-white px-1 text-xs"
                  value={selectedRevision} onChange={(event) => setSelectedRevision(event.target.value)}>
                  <option value="">当前版本</option>
                  {revisions.filter((item) => !item.is_current).map((item) => <option key={item.revision_id} value={item.revision_id}>
                    {item.state === "validated" ? "候选" : "历史"} · {item.revision_id}
                  </option>)}
                </select>}
              </div>
              {recognitionLabel && <div className="mt-0.5 text-xs text-slate-500" data-testid="recognition-state">{recognitionLabel}</div>}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span
            title={task.reviewReasons?.join("；") || undefined}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs font-medium",
              task.status === "completed"
                ? task.qualityStatus === "degraded"
                  ? "border-orange-200 bg-orange-50 text-orange-700"
                  : "border-success-200 bg-success-50 text-success-700"
                : task.status === "review_required"
                  ? "border-orange-200 bg-orange-50 text-orange-700"
                  : task.status === "analyzing"
                    ? "border-warning-200 bg-warning-50 text-warning-700"
                    : "border-danger-200 bg-danger-50 text-danger-700"
            )}
          >
            {task.status === "completed"
              ? task.qualityStatus === "degraded"
                ? "分析完成（部分降级）"
                : task.analysisConclusion === "no_findings"
                  ? "分析完成（未发现问题）"
                  : "分析完成"
              : task.status === "review_required"
                ? "需人工复核"
                : task.status === "analyzing"
                  ? "分析中"
                  : "分析失败"}
          </span>
          <div className="mx-2 h-6 w-px bg-border" />
          <button
            type="button"
            data-testid="task-associate-button"
            onClick={() => setIsAssociateDialogOpen(true)}
            disabled={isAssociating || Boolean(selectedRevision)}
            className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Link2 className="h-4 w-4 text-slate-400" />
            {jobDetail?.organization_id ? "更改关联" : "关联报告"}
          </button>
          <button
            type="button"
            data-testid="task-reanalyze-button"
            onClick={() => void handleReanalyze()}
            disabled={isReanalyzing || task.status === "analyzing" || Boolean(selectedRevision)}
            className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className="h-4 w-4 text-slate-400" />
            {isReanalyzing ? "重新分析中..." : task.status === "analyzing" ? "分析中" : "重新分析"}
          </button>
          <button
            type="button"
            data-testid="task-open-report-modal"
            onClick={() => setIsReportModalOpen(true)}
            className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
          >
            <Download className="h-4 w-4 text-slate-400" />
            导出报告
          </button>
        </div>
      </div>

      <div className="border-b border-border bg-slate-50 px-6 py-3">
        <AnalysisCheckProgress value={jobDetail?.check_progress} />
        <div className="flex justify-end">
          <ReanalyzeAiToggle
            checked={effectiveReanalyzeUseAiAssist}
            onChange={setReanalyzeUseAiAssist}
            disabled={Boolean(aiOptions.capabilities && !aiOptions.capabilities.enabled)}
            testId="task-reanalyze-ai-toggle"
            className="w-full max-w-md bg-white"
            description={aiOptions.error || (aiOptions.capabilities?.enabled
              ? aiOptions.enabled
                ? "重新分析将使用系统当前启用的模型；取消勾选后仅执行本地检查。"
                : "模型已配置，但本次默认关闭；勾选后重新分析会调用大模型。"
              : "当前没有可用的大模型配置，重新分析将仅执行本地检查。")}
          />
        </div>
      </div>

      <div className="relative flex flex-1 overflow-hidden">
        <PipelineDrawer
          isOpen={isDrawerOpen}
          onToggle={() => setIsDrawerOpen((prev) => !prev)}
          task={task}
        />

        <ProblemSidebar
          problems={filteredProblems}
          selectedId={selectedProblemId}
          onSelect={setSelectedProblemId}
          searchValue={problemSearchValue}
          onSearchChange={setProblemSearchValue}
          categories={problemCategories}
          activeCategory={problemCategory}
          onCategoryChange={setProblemCategory}
          highRiskOnly={highRiskOnly}
          onToggleHighRiskOnly={() => setHighRiskOnly((current) => !current)}
        />

        <div className="flex-1 overflow-y-auto bg-slate-50/50 p-6">
          {highlightedProblem ? (
            <EvidencePanel
              problem={highlightedProblem}
              onOpenViewer={() => setIsViewerOpen(true)}
              selectedEvidenceRefKey={selectedEvidenceRef && selectedEvidenceRef.problemId === selectedProblem?.id
                ? selectedEvidenceRef.refKey
                : null}
              onSelectEvidenceRef={(refKey) => {
                if (selectedProblem) setSelectedEvidenceRef({ problemId: selectedProblem.id, refKey });
              }}
              onIgnore={selectedRevision ? undefined : () => void handleIgnoreProblem()}
              isIgnoring={ignoringProblemId === highlightedProblem.id}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-slate-400">
              <FileText className="mb-4 h-12 w-12 text-slate-300" />
              <p className="text-sm font-medium">
                {problems.length === 0
                  ? "当前任务暂无问题。"
                  : "当前筛选条件下没有问题。"}
              </p>
            </div>
          )}
        </div>
      </div>

      {isViewerOpen && highlightedProblem ? (
        <PDFHighlighter
          problem={highlightedProblem}
          onClose={() => setIsViewerOpen(false)}
        />
      ) : null}

      {isReportModalOpen ? (
        <ReportPreviewModal
          task={task}
          problems={filteredProblems}
          onClose={() => setIsReportModalOpen(false)}
        />
      ) : null}
      {isAssociateDialogOpen && jobDetail ? (
        <AssociateDialog
          isOpen
          jobId={jobDetail.job_id}
          filename={String(jobDetail.filename ?? jobDetail.job_id)}
          isSubmitting={isAssociating}
          onClose={() => {
            if (!isAssociating) {
              setIsAssociateDialogOpen(false);
            }
          }}
          onAssociate={handleAssociate}
        />
      ) : null}
    </div>
  );
}
