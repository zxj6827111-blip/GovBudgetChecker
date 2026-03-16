"use client";

import type { Route } from "next";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Download, Filter, PencilLine, RefreshCw, UploadCloud } from "lucide-react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import AssociateDialog from "@/components/AssociateDialog";
import BatchUploadModal from "@/components/BatchUploadModal";
import ReanalyzeAiToggle from "@/components/ReanalyzeAiToggle";
import { ORG_TREE_REFRESH_EVENT, dispatchOrgTreeRefresh } from "@/lib/orgTreeEvents";
import { cn } from "@/lib/utils";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import {
  getDisplayIssueTotal,
  getHighRiskCount,
  normalizeUiTaskStatus,
  toUiTask,
} from "@/lib/uiAdapters";
import DepartmentJobTable from "./DepartmentJobTable";
import {
  type AdvancedFilters,
  type DepartmentTab,
  downloadBlob,
  escapeCsvCell,
  fetchJson,
  getDispositionFilename,
  needsIngestReview,
  normalizeSearchValue,
  readErrorMessage,
} from "./helpers";

type OrganizationSummary = {
  id: string;
  name: string;
  level: string;
  level_name?: string;
  parent_id?: string | null;
};

type OrganizationsResponse = {
  organizations?: OrganizationSummary[];
};

type OrganizationJobsResponse = {
  jobs?: JobSummaryRecord[];
};

type SearchStatus = "all" | "completed" | "analyzing" | "failed" | "review";

const defaultAdvancedFilters: AdvancedFilters = {
  highRiskOnly: false,
  unlinkedOnly: false,
  pendingReviewOnly: false,
};

function getSearchStatusLabel(status: SearchStatus) {
  if (status === "completed") return "已完成 completed";
  if (status === "failed") return "失败 failed";
  if (status === "review") return "待复核 review";
  if (status === "analyzing") return "分析中 analyzing";
  return "全部 all";
}

function collectExportRows(job: JobSummaryRecord, payload: any) {
  const issues = Array.isArray(payload?.issues) ? payload.issues : [];
  if (issues.length === 0) {
    return [
      [
        job.organization_name ?? "",
        job.filename ?? "",
        job.job_id,
        job.report_year ?? "",
        job.report_kind ?? "",
        normalizeUiTaskStatus(job.status),
        getDisplayIssueTotal(job),
        getHighRiskCount(job),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
      ],
    ];
  }

  return issues.map((issue: any) => {
    const location = issue?.export_location ?? {};
    return [
      job.organization_name ?? "",
      job.filename ?? "",
      job.job_id,
      job.report_year ?? "",
      job.report_kind ?? "",
      normalizeUiTaskStatus(job.status),
      getDisplayIssueTotal(job),
      getHighRiskCount(job),
      issue?.id ?? "",
      issue?.rule_id ?? issue?.rule ?? "",
      issue?.title ?? issue?.message ?? "",
      issue?.severity ?? "",
      issue?.page ?? location?.page ?? "",
      location?.role_summary ?? "",
      location?.table ?? "",
      issue?.suggestion ?? "",
    ];
  });
}

export default function DepartmentPageClient() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchQuery = searchParams.get("q") ?? "";
  const normalizedSearchQuery = useMemo(
    () => normalizeSearchValue(searchQuery),
    [searchQuery],
  );

  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  const [jobs, setJobs] = useState<JobSummaryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [includeSub, setIncludeSub] = useState(true);
  const [activeTab, setActiveTab] = useState<DepartmentTab>("budget");
  const [selectedYear, setSelectedYear] = useState("all");
  const [selectedStatus, setSelectedStatus] = useState<SearchStatus>("all");
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
  const [advancedFilters, setAdvancedFilters] = useState(defaultAdvancedFilters);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null);
  const [reanalyzingJobId, setReanalyzingJobId] = useState<string | null>(null);
  const [exportingJobId, setExportingJobId] = useState<string | null>(null);
  const [isBatchDeleting, setIsBatchDeleting] = useState(false);
  const [isBatchReanalyzing, setIsBatchReanalyzing] = useState(false);
  const [isBatchExporting, setIsBatchExporting] = useState(false);
  const [isBatchZipExporting, setIsBatchZipExporting] = useState(false);
  const [reanalyzeUseAiAssist, setReanalyzeUseAiAssist] = useState(true);
  const [hasConfiguredReanalyzeUseAiAssist, setHasConfiguredReanalyzeUseAiAssist] = useState(false);
  const [associatingJobId, setAssociatingJobId] = useState<string | null>(null);
  const [isAssociatingJob, setIsAssociatingJob] = useState(false);
  const [isRenameModalOpen, setIsRenameModalOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isRenamingOrg, setIsRenamingOrg] = useState(false);
  const [refreshSeed, setRefreshSeed] = useState(0);

  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setOpenMenuId(null);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    const handleTreeRefresh = () => {
      setRefreshSeed((current) => current + 1);
    };

    window.addEventListener(ORG_TREE_REFRESH_EVENT, handleTreeRefresh);
    return () => window.removeEventListener(ORG_TREE_REFRESH_EVENT, handleTreeRefresh);
  }, []);

  useEffect(() => {
    let alive = true;

    async function load() {
      setLoading(true);
      const [organizationPayload, jobsPayload] = await Promise.all([
        fetchJson<OrganizationsResponse>("/api/organizations/list", { organizations: [] }),
        fetchJson<OrganizationJobsResponse>(
          `/api/organizations/${encodeURIComponent(id)}/jobs?include_children=${includeSub ? "true" : "false"}`,
          { jobs: [] },
        ),
      ]);

      if (!alive) {
        return;
      }

      setOrganizations(
        Array.isArray(organizationPayload.organizations) ? organizationPayload.organizations : [],
      );
      setJobs(Array.isArray(jobsPayload.jobs) ? jobsPayload.jobs : []);
      setSelectedTasks([]);
      setOpenMenuId(null);
      setLoading(false);
    }

    void load();
    return () => {
      alive = false;
    };
  }, [id, includeSub, refreshSeed]);

  const currentOrg = useMemo(
    () => organizations.find((org) => org.id === id) ?? null,
    [id, organizations],
  );

  useEffect(() => {
    if (currentOrg?.level === "unit" && includeSub) {
      setIncludeSub(false);
    }
  }, [currentOrg?.level, includeSub]);

  const associatingJob = useMemo(
    () => jobs.find((job) => job.job_id === associatingJobId) ?? null,
    [associatingJobId, jobs],
  );

  const pendingReviewCount = useMemo(
    () => jobs.filter((job) => needsIngestReview(job)).length,
    [jobs],
  );

  const totalIssueCount = useMemo(
    () => jobs.reduce((sum, job) => sum + getDisplayIssueTotal(job), 0),
    [jobs],
  );

  useEffect(() => {
    const hasBudgetJobs = jobs.some((job) => job.report_kind === "budget");
    const hasFinalJobs = jobs.some((job) => job.report_kind === "final");
    const hasReviewJobs = jobs.some((job) => needsIngestReview(job));

    setActiveTab((current) => {
      if (current === "budget" && hasBudgetJobs) return current;
      if (current === "final" && hasFinalJobs) return current;
      if (current === "review" && hasReviewJobs) return current;
      if (hasBudgetJobs) return "budget";
      if (hasFinalJobs) return "final";
      if (hasReviewJobs) return "review";
      return "budget";
    });
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
      if (activeTab === "review") {
        if (!needsIngestReview(job)) return false;
      } else if (job.report_kind !== activeTab) {
        return false;
      }

      if (selectedYear !== "all" && String(job.report_year ?? "") !== selectedYear) {
        return false;
      }

      const status = normalizeUiTaskStatus(job.status);
      if (selectedStatus === "review") {
        if (!needsIngestReview(job)) return false;
      } else if (selectedStatus !== "all" && status !== selectedStatus) {
        return false;
      }

      if (normalizedSearchQuery) {
        const task = toUiTask(job);
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

      if (advancedFilters.highRiskOnly && getHighRiskCount(job) <= 0) {
        return false;
      }
      if (advancedFilters.unlinkedOnly && String(job.organization_id ?? "").trim()) {
        return false;
      }
      if (advancedFilters.pendingReviewOnly && !needsIngestReview(job)) {
        return false;
      }

      return true;
    });
  }, [activeTab, advancedFilters, jobs, normalizedSearchQuery, selectedStatus, selectedYear]);

  const activeAdvancedFilterCount = useMemo(
    () => Object.values(advancedFilters).filter(Boolean).length,
    [advancedFilters],
  );
  const reanalyzeRequestBody = hasConfiguredReanalyzeUseAiAssist
    ? {
        use_local_rules: true,
        use_ai_assist: reanalyzeUseAiAssist,
      }
    : {};

  useEffect(() => {
    const visibleTaskIds = new Set(filteredTasks.map((task) => task.job_id));
    setSelectedTasks((current) => current.filter((taskId) => visibleTaskIds.has(taskId)));
    if (openMenuId && !visibleTaskIds.has(openMenuId)) {
      setOpenMenuId(null);
    }
  }, [filteredTasks, openMenuId]);

  const toggleSelect = (taskId: string) => {
    setSelectedTasks((current) =>
      current.includes(taskId)
        ? current.filter((item) => item !== taskId)
        : [...current, taskId],
    );
  };

  const toggleAll = () => {
    if (selectedTasks.length === filteredTasks.length) {
      setSelectedTasks([]);
      return;
    }
    setSelectedTasks(filteredTasks.map((task) => task.job_id));
  };

  const openAssociateDialog = (jobId: string, event?: ReactMouseEvent<HTMLButtonElement>) => {
    event?.stopPropagation();
    setOpenMenuId(null);
    setAssociatingJobId(jobId);
  };

  const closeAssociateDialog = () => {
    if (!isAssociatingJob) {
      setAssociatingJobId(null);
    }
  };

  const handleRenameOrganization = async () => {
    const nextName = renameValue.trim();
    if (!currentOrg || !nextName || isRenamingOrg) return;

    setIsRenamingOrg(true);
    try {
      const response = await fetch(`/api/organizations/${encodeURIComponent(currentOrg.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nextName }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      setOrganizations((current) =>
        current.map((org) => (org.id === currentOrg.id ? { ...org, name: nextName } : org)),
      );
      setJobs((current) =>
        current.map((job) =>
          job.organization_id === currentOrg.id ? { ...job, organization_name: nextName } : job,
        ),
      );
      setIsRenameModalOpen(false);
      setRenameValue("");
      dispatchOrgTreeRefresh();
      alert(`组织名称已更新为 ${nextName}`);
    } catch (error) {
      console.error("Failed to rename organization:", error);
      alert(error instanceof Error ? error.message : "修改名称失败，请稍后重试。");
    } finally {
      setIsRenamingOrg(false);
    }
  };

  const handleDelete = async (jobId: string, event: ReactMouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    setOpenMenuId(null);
    if (!confirm("确定要删除这份报告吗？此操作不可恢复。")) return;

    setDeletingJobId(jobId);
    try {
      const response = await fetch("/api/jobs/batch-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: [jobId] }),
      });
      const payload = (await response.json().catch(() => ({}))) as {
        deleted_job_ids?: string[];
        failed?: Array<{ detail?: string }>;
      };
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const deletedIds = Array.isArray(payload.deleted_job_ids) ? payload.deleted_job_ids : [jobId];
      const deletedSet = new Set(deletedIds);
      setJobs((current) => current.filter((job) => !deletedSet.has(job.job_id)));
      setSelectedTasks((current) => current.filter((taskId) => !deletedSet.has(taskId)));
      dispatchOrgTreeRefresh();
      alert(payload.failed?.length ? "报告已部分删除。" : "报告已删除。");
    } catch (error) {
      console.error("Failed to delete report:", error);
      alert(error instanceof Error ? error.message : "删除报告失败，请稍后重试。");
    } finally {
      setDeletingJobId(null);
    }
  };

  const handleReanalyze = async (jobId: string, event?: ReactMouseEvent<HTMLButtonElement>) => {
    event?.stopPropagation();
    setOpenMenuId(null);
    if (!confirm("确定要重新分析这份报告吗？")) return;

    setReanalyzingJobId(jobId);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/reanalyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reanalyzeRequestBody),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      setJobs((current) =>
        current.map((job) =>
          job.job_id === jobId
            ? {
                ...job,
                status: "started",
                updated_ts: Math.floor(Date.now() / 1000),
                use_local_rules: hasConfiguredReanalyzeUseAiAssist ? true : job.use_local_rules,
                use_ai_assist: hasConfiguredReanalyzeUseAiAssist
                  ? reanalyzeUseAiAssist
                  : job.use_ai_assist,
              }
            : job,
        ),
      );
      alert("已触发重新分析。");
    } catch (error) {
      console.error("Failed to reanalyze report:", error);
      alert(error instanceof Error ? error.message : "重新分析失败，请稍后重试。");
    } finally {
      setReanalyzingJobId(null);
    }
  };

  const handleDownloadReport = async (
    jobId: string,
    fallbackName: string,
    event?: ReactMouseEvent<HTMLButtonElement>,
  ) => {
    event?.stopPropagation();
    setOpenMenuId(null);
    setExportingJobId(jobId);
    try {
      const response = await fetch(
        `/api/reports/download?job_id=${encodeURIComponent(jobId)}&format=pdf`,
      );
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const blob = await response.blob();
      const filename = getDispositionFilename(
        response.headers.get("content-disposition"),
        `${fallbackName.replace(/\.[^.]+$/u, "") || jobId}.pdf`,
      );
      downloadBlob(blob, filename);
    } catch (error) {
      console.error("Failed to download report:", error);
      alert(error instanceof Error ? error.message : "导出报告失败，请稍后重试。");
    } finally {
      setExportingJobId(null);
    }
  };

  const handleBatchReanalyze = async () => {
    const selectedJobs = filteredTasks.filter((job) => selectedTasks.includes(job.job_id));
    const eligibleJobs = selectedJobs.filter(
      (job) => normalizeUiTaskStatus(job.status) !== "analyzing",
    );

    if (eligibleJobs.length === 0) {
      alert("选中的报告都已在分析中。");
      return;
    }
    if (!confirm(`确定要批量重新分析 ${eligibleJobs.length} 份报告吗？`)) return;

    setIsBatchReanalyzing(true);
    try {
      const results = await Promise.allSettled(
        eligibleJobs.map(async (job) => {
          const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/reanalyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(reanalyzeRequestBody),
          });
          if (!response.ok) {
            throw new Error(await readErrorMessage(response));
          }
          return job.job_id;
        }),
      );

      const succeeded = results
        .filter((item): item is PromiseFulfilledResult<string> => item.status === "fulfilled")
        .map((item) => item.value);
      const successSet = new Set(succeeded);
      const failedCount = results.length - succeeded.length;

      setJobs((current) =>
        current.map((job) =>
          successSet.has(job.job_id)
            ? {
                ...job,
                status: "started",
                updated_ts: Math.floor(Date.now() / 1000),
                use_local_rules: hasConfiguredReanalyzeUseAiAssist ? true : job.use_local_rules,
                use_ai_assist: hasConfiguredReanalyzeUseAiAssist
                  ? reanalyzeUseAiAssist
                  : job.use_ai_assist,
              }
            : job,
        ),
      );
      setSelectedTasks((current) => current.filter((taskId) => !successSet.has(taskId)));
      alert(
        failedCount > 0
          ? `已触发 ${succeeded.length} 份报告重新分析，失败 ${failedCount} 份。`
          : `已触发 ${succeeded.length} 份报告重新分析。`,
      );
    } catch (error) {
      console.error("Failed to batch reanalyze reports:", error);
      alert(error instanceof Error ? error.message : "批量重新分析失败，请稍后重试。");
    } finally {
      setIsBatchReanalyzing(false);
    }
  };

  const handleBatchExport = async () => {
    const selectedJobs = filteredTasks.filter((job) => selectedTasks.includes(job.job_id));
    if (selectedJobs.length === 0) {
      alert("请先选择要导出的报告。");
      return;
    }

    setIsBatchExporting(true);
    try {
      const results = await Promise.allSettled(
        selectedJobs.map(async (job) => {
          const response = await fetch(
            `/api/reports/download?job_id=${encodeURIComponent(job.job_id)}&format=json`,
          );
          if (!response.ok) {
            throw new Error(await readErrorMessage(response));
          }
          return { job, payload: await response.json() };
        }),
      );

      const succeeded = results.filter(
        (
          item,
        ): item is PromiseFulfilledResult<{ job: JobSummaryRecord; payload: any }> =>
          item.status === "fulfilled",
      );
      if (succeeded.length === 0) {
        throw new Error("选中的报告都未能成功导出。");
      }

      const header = [
        "organization_name",
        "filename",
        "job_id",
        "report_year",
        "report_kind",
        "status",
        "issue_total",
        "high_risk_total",
        "issue_id",
        "rule_id",
        "title",
        "severity",
        "page",
        "location",
        "table",
        "suggestion",
      ];
      const rows: Array<Array<string | number>> = [header];
      succeeded.forEach(({ value }) => {
        collectExportRows(value.job, value.payload).forEach((row: Array<string | number>) =>
          rows.push(row),
        );
      });

      const csvText = `\ufeff${rows
        .map((row: Array<string | number>) => row.map((cell) => escapeCsvCell(cell)).join(","))
        .join("\r\n")}`;
      const scopeName = currentOrg?.name?.trim() || "department";
      const filename = `${scopeName}-${new Date().toISOString().slice(0, 10)}-reports.csv`;
      downloadBlob(new Blob([csvText], { type: "text/csv;charset=utf-8;" }), filename);

      const failedCount = results.length - succeeded.length;
      alert(
        failedCount > 0
          ? `已导出 ${succeeded.length} 份报告，另有 ${failedCount} 份导出失败。`
          : `已导出 ${succeeded.length} 份报告。`,
      );
    } catch (error) {
      console.error("Failed to batch export reports:", error);
      alert(error instanceof Error ? error.message : "批量导出失败，请稍后重试。");
    } finally {
      setIsBatchExporting(false);
    }
  };

  const handleBatchExportZip = async () => {
    const selectedJobs = filteredTasks.filter((job) => selectedTasks.includes(job.job_id));
    if (selectedJobs.length === 0) {
      alert("请先选择要导出的报告。");
      return;
    }

    setIsBatchZipExporting(true);
    try {
      const response = await fetch("/api/reports/download-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: selectedJobs.map((job) => job.job_id) }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const blob = await response.blob();
      const scopeName = currentOrg?.name?.trim() || "department";
      const fallbackFilename = `${scopeName}-${new Date().toISOString().slice(0, 10)}-reports.zip`;
      const filename = getDispositionFilename(
        response.headers.get("content-disposition"),
        fallbackFilename,
      );
      downloadBlob(blob, filename);
      alert(`已打包导出 ${selectedJobs.length} 份 PDF 报告。`);
    } catch (error) {
      console.error("Failed to batch export report pdf zip:", error);
      alert(error instanceof Error ? error.message : "批量导出 PDF 失败，请稍后重试。");
    } finally {
      setIsBatchZipExporting(false);
    }
  };

  const handleBatchDelete = async () => {
    if (selectedTasks.length === 0) {
      alert("请先选择要删除的报告。");
      return;
    }
    if (!confirm(`确定要批量删除选中的 ${selectedTasks.length} 份报告吗？此操作不可恢复。`)) {
      return;
    }

    setIsBatchDeleting(true);
    try {
      const response = await fetch("/api/jobs/batch-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: selectedTasks }),
      });
      const payload = (await response.json().catch(() => ({}))) as {
        deleted_job_ids?: string[];
        failed?: Array<{ detail?: string }>;
      };
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const deletedIds = Array.isArray(payload.deleted_job_ids)
        ? payload.deleted_job_ids
        : selectedTasks;
      const deletedSet = new Set(deletedIds);
      setJobs((current) => current.filter((job) => !deletedSet.has(job.job_id)));
      setSelectedTasks([]);
      dispatchOrgTreeRefresh();
      alert(payload.failed?.length ? "报告已部分删除。" : "选中报告已删除。");
    } catch (error) {
      console.error("Failed to batch delete reports:", error);
      alert(error instanceof Error ? error.message : "批量删除失败，请稍后重试。");
    } finally {
      setIsBatchDeleting(false);
    }
  };

  const handleAssociateJob = async (orgId: string) => {
    if (!associatingJob) return;

    setIsAssociatingJob(true);
    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(associatingJob.job_id)}/associate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ org_id: orgId }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as {
        organization_id?: string;
        organization_name?: string;
        organization_match_type?: string;
        organization_match_confidence?: number;
      };
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const nextOrganizationName = String(payload.organization_name ?? "").trim();
      const nextOrganizationId = String(payload.organization_id ?? "").trim();
      setJobs((current) =>
        current.map((job) =>
          job.job_id === associatingJob.job_id
            ? {
                ...job,
                organization_id: nextOrganizationId || job.organization_id,
                organization_name: nextOrganizationName || job.organization_name,
                organization_match_type:
                  payload.organization_match_type || job.organization_match_type,
                organization_match_confidence:
                  typeof payload.organization_match_confidence === "number"
                    ? payload.organization_match_confidence
                    : job.organization_match_confidence,
              }
            : job,
        ),
      );
      setAssociatingJobId(null);
      dispatchOrgTreeRefresh();
      alert(nextOrganizationName ? `报告已关联到 ${nextOrganizationName}` : "报告关联已更新。");
    } catch (error) {
      console.error("Failed to associate report:", error);
      alert(error instanceof Error ? error.message : "关联报告失败，请稍后重试。");
    } finally {
      setIsAssociatingJob(false);
    }
  };

  const uploadTargetOrgId = currentOrg?.id;
  const includeDescendantsLabel =
    currentOrg?.level === "department" ? "包含下属单位" : "包含下级组织";

  return (
    <>
      <div className="mx-auto max-w-7xl p-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm text-slate-500">
              <Link href={"/" as Route} className="hover:text-primary-600">
                首页
              </Link>
              <span className="mx-2 text-slate-300">/</span>
              <span>{currentOrg?.level === "unit" ? "单位详情" : "部门详情"}</span>
            </p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-900">
              {currentOrg?.name || "组织详情"}
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              当前共 {jobs.length} 份报告，问题总数 {totalIssueCount}，待复核 {pendingReviewCount}
              {normalizedSearchQuery ? `，搜索关键词：${searchQuery}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="org-rename-button"
              onClick={() => {
                setRenameValue(currentOrg?.name ?? "");
                setIsRenameModalOpen(true);
              }}
              className="flex items-center gap-2 rounded-lg border border-border bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
            >
              <PencilLine className="h-4 w-4" />
              修改名称
            </button>
            <div className="hidden h-6 w-px bg-border md:block" />
            <label
              className={cn(
                "inline-flex items-center gap-3",
                currentOrg?.level === "unit"
                  ? "cursor-not-allowed opacity-60"
                  : "cursor-pointer",
              )}
            >
              <div className="relative">
                <input
                  type="checkbox"
                  className="sr-only"
                  data-testid="toggle-include-sub"
                  checked={includeSub}
                  disabled={currentOrg?.level === "unit"}
                  onChange={() => setIncludeSub((current) => !current)}
                  aria-label={includeDescendantsLabel}
                />
                <div
                  className={cn(
                    "block h-6 w-10 rounded-full transition-colors",
                    includeSub ? "bg-primary-600" : "bg-slate-300",
                  )}
                />
                <div
                  className={cn(
                    "absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition-transform",
                    includeSub && "translate-x-4",
                  )}
                />
              </div>
              <span className="text-sm font-medium text-slate-700">{includeDescendantsLabel}</span>
            </label>
            <button
              type="button"
              onClick={() => setIsUploadModalOpen(true)}
              className="flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-700"
            >
              <UploadCloud className="h-4 w-4" />
              上传报告
            </button>
          </div>
        </div>

        <div className="mt-8 rounded-2xl border border-border bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center gap-3">
            {([
              { id: "budget", label: "预算报告" },
              { id: "final", label: "决算报告" },
              { id: "review", label: "待复核" },
            ] as Array<{ id: DepartmentTab; label: string }>).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "rounded-full px-4 py-2 text-sm font-medium transition-colors",
                  activeTab === tab.id
                    ? "bg-primary-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200",
                )}
              >
                {tab.label}
              </button>
            ))}

            <label className="ml-auto hidden items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={includeSub}
                disabled={currentOrg?.level === "unit"}
                onChange={() => setIncludeSub((current) => !current)}
                className="rounded border-slate-300 text-primary-600 focus:ring-primary-500"
              />
              包含下级组织
            </label>
            <button
              type="button"
              data-testid="toggle-include-sub-legacy"
              disabled={currentOrg?.level === "unit"}
              onClick={() => setIncludeSub((current) => !current)}
              className={cn(
                "ml-auto hidden items-center rounded-full border px-4 py-2 text-sm font-medium transition-colors",
                currentOrg?.level === "unit"
                  ? "cursor-not-allowed border-slate-200 bg-slate-100 text-slate-400"
                  : includeSub
                    ? "border-primary-200 bg-primary-50 text-primary-700 hover:bg-primary-100"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
              )}
              title={
                currentOrg?.level === "unit"
                  ? "当前为单位层级，无下级组织可展开"
                  : includeSub
                    ? "点击后关闭下级组织"
                    : "点击后包含下级组织"
              }
            >
              {currentOrg?.level === "unit"
                ? "当前单位无下级组织"
                : includeSub
                  ? "包含下级组织：开"
                  : "包含下级组织：关"}
            </button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <select
              value={selectedYear}
              onChange={(event) => setSelectedYear(event.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="all">全部年度</option>
              {years.map((year) => (
                <option key={year} value={String(year)}>
                  {year} 年
                </option>
              ))}
            </select>
            <select
              value={selectedStatus}
              onChange={(event) => setSelectedStatus(event.target.value as SearchStatus)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700"
            >
              <option value="all">全部状态</option>
              <option value="completed">已完成</option>
              <option value="analyzing">分析中</option>
              <option value="failed">失败</option>
              <option value="review">待复核</option>
            </select>
            <button
              type="button"
              onClick={() => setShowAdvancedFilters((current) => !current)}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
                showAdvancedFilters || activeAdvancedFilterCount > 0
                  ? "border-primary-200 bg-primary-50 text-primary-700"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
              )}
            >
              <Filter className="h-4 w-4" />
              高级筛选
              {activeAdvancedFilterCount > 0 ? (
                <span className="rounded-full bg-primary-600 px-2 py-0.5 text-xs text-white">
                  {activeAdvancedFilterCount}
                </span>
              ) : null}
            </button>
          </div>

          {showAdvancedFilters ? (
            <div className="mt-4 flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={advancedFilters.highRiskOnly}
                  onChange={() =>
                    setAdvancedFilters((current) => ({
                      ...current,
                      highRiskOnly: !current.highRiskOnly,
                    }))
                  }
                />
                仅看高风险
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={advancedFilters.unlinkedOnly}
                  onChange={() =>
                    setAdvancedFilters((current) => ({
                      ...current,
                      unlinkedOnly: !current.unlinkedOnly,
                    }))
                  }
                />
                仅看未关联
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={advancedFilters.pendingReviewOnly}
                  onChange={() =>
                    setAdvancedFilters((current) => ({
                      ...current,
                      pendingReviewOnly: !current.pendingReviewOnly,
                    }))
                  }
                />
                仅看待复核
              </label>
            </div>
          ) : null}

          <ReanalyzeAiToggle
            checked={reanalyzeUseAiAssist}
            onChange={(checked) => {
              setReanalyzeUseAiAssist(checked);
              setHasConfiguredReanalyzeUseAiAssist(true);
            }}
            className="mt-4"
            testId="department-reanalyze-ai-toggle"
            description="当前页面的单个重新分析和批量重新分析都会使用这个设置；取消后仅本地解析。"
          />
        </div>

        {selectedTasks.length > 0 ? (
          <div
            data-testid="selected-actions-bar"
            className="mt-6 flex flex-wrap items-center gap-3 rounded-2xl border border-primary-200 bg-primary-50 px-5 py-4"
          >
            <span className="text-sm font-medium text-primary-800">
              已选择 {selectedTasks.length} 份报告
            </span>
            <button
              type="button"
              onClick={() => void handleBatchReanalyze()}
              disabled={isBatchReanalyzing}
              className="inline-flex items-center gap-2 rounded-lg border border-primary-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 disabled:opacity-60"
            >
              <RefreshCw className="h-4 w-4" />
              {isBatchReanalyzing ? "批量重分析中..." : "批量重分析"}
            </button>
            <button
              type="button"
              data-testid="batch-export-button"
              onClick={() => void handleBatchExport()}
              disabled={isBatchExporting}
              className="inline-flex items-center gap-2 rounded-lg border border-primary-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 disabled:opacity-60"
            >
              <Download className="h-4 w-4" />
              {isBatchExporting ? "批量导出中..." : "批量导出"}
            </button>
            <button
              type="button"
              data-testid="batch-export-zip-button"
              onClick={() => void handleBatchExportZip()}
              disabled={isBatchZipExporting}
              className="inline-flex items-center gap-2 rounded-lg border border-primary-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 disabled:opacity-60"
            >
              <Download className="h-4 w-4" />
              {isBatchZipExporting ? "打包中..." : "打包导出 PDF"}
            </button>
            <button
              type="button"
              data-testid="batch-delete-button"
              onClick={() => void handleBatchDelete()}
              disabled={isBatchDeleting}
              className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {isBatchDeleting ? "批量删除中..." : "批量删除"}
            </button>
          </div>
        ) : null}

        <div className="mt-6">
          <DepartmentJobTable
            jobs={filteredTasks}
            loading={loading}
            normalizedSearchQuery={normalizedSearchQuery}
            selectedTasks={selectedTasks}
            openMenuId={openMenuId}
            menuRef={menuRef}
            routerPush={(href) => router.push(href as Route)}
            toggleAll={toggleAll}
            toggleSelect={toggleSelect}
            setOpenMenuId={setOpenMenuId}
            openAssociateDialog={openAssociateDialog}
            handleReanalyze={handleReanalyze}
            handleDownloadReport={handleDownloadReport}
            handleDelete={handleDelete}
            deletingJobId={deletingJobId}
            reanalyzingJobId={reanalyzingJobId}
            exportingJobId={exportingJobId}
            isAssociatingJob={isAssociatingJob}
            associatingJobId={associatingJobId}
          />
        </div>
      </div>

      {isUploadModalOpen ? (
        <BatchUploadModal
          orgUnitId={uploadTargetOrgId}
          defaultDocType="dept_budget"
          onClose={() => setIsUploadModalOpen(false)}
          onComplete={() => {
            setIsUploadModalOpen(false);
            setRefreshSeed((current) => current + 1);
            dispatchOrgTreeRefresh();
          }}
        />
      ) : null}

      {associatingJob ? (
        <AssociateDialog
          isOpen
          jobId={associatingJob.job_id}
          filename={String(associatingJob.filename ?? associatingJob.job_id)}
          isSubmitting={isAssociatingJob}
          onClose={closeAssociateDialog}
          onAssociate={handleAssociateJob}
        />
      ) : null}

      {isRenameModalOpen && currentOrg ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 px-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
            <h2 className="text-lg font-semibold text-slate-900">
              {currentOrg.level === "unit" ? "修改单位名称" : "修改部门名称"}
            </h2>
            <p className="mt-2 text-sm text-slate-500">
              修改后会同步到组织树和当前页面的报告归属展示。
            </p>
            <input
              type="text"
              autoFocus
              data-testid="org-rename-input"
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void handleRenameOrganization();
                }
              }}
              className="mt-4 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
            <div className="mt-5 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  if (!isRenamingOrg) {
                    setIsRenameModalOpen(false);
                    setRenameValue("");
                  }
                }}
                disabled={isRenamingOrg}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600"
              >
                取消
              </button>
              <button
                type="button"
                data-testid="org-rename-submit"
                onClick={() => void handleRenameOrganization()}
                disabled={!renameValue.trim() || isRenamingOrg}
                className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {isRenamingOrg ? "保存中..." : "保存"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
