"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import IssueTabs, { DualModeResult, IssueItem } from "./components/IssueTabs";
import IssueList from "./components/IssueList";
import IssueCard from "./components/IssueCard";
import ActionAccordion from "./components/ActionAccordion";
import OrganizationTree from "./components/OrganizationTree";
import OrganizationDetailView from "./components/OrganizationDetailView";
import AssociateDialog from "./components/AssociateDialog";
import PipelineStatus from "./components/PipelineStatus";
import BatchUploadModal from "./components/BatchUploadModal";
import QCResultView from "./components/QCResultView";
import StructuredCleanupDialog, {
  StructuredCleanupPreviewPayload,
} from "./components/StructuredCleanupDialog";
import StructuredIngestPanel, { StructuredIngestPayload } from "./components/StructuredIngestPanel";
import { format } from 'date-fns';

type UploadResp = {
  job_id: string;
  filename?: string;
  size?: number;
  saved_path?: string;
  checksum?: string;
  organization_id?: string;
  organization_name?: string;
  organization_match_type?: string;
  organization_match_confidence?: number;
};

// Updated JobStatus to support V3 pipeline stages
type JobStatus =
  | {
    job_id: string;
    status: "queued" | "processing" | "running";
    progress?: number;
    ts?: number;
    stages?: any; // New field for V3
    current_stage?: string; // New field for V3
  }
  | {
    job_id: string;
    status: "done" | "completed";  // "completed" is used by V3
    progress: 100;
    result: ResultPayload;
    ts?: number;
    stages?: any; // New field for V3
    current_stage?: string; // New field for V3
    report_path?: string;
    qc_run_id?: number;
  }
  | { job_id: string; status: "error" | "failed"; error: string; ts?: number; stages?: any; failure_reason?: string }
  | { job_id: string; status: "unknown" }
  | { job_id: string; status: "ready" };

type Issue = any;

type ResultPayload = {
  summary: string;
  issues: IssueItem[] | { error: IssueItem[]; warn: IssueItem[]; info: IssueItem[]; all: IssueItem[] };
  meta?: Record<string, any>;
  dual_mode?: DualModeResult;
  mode?: "local" | "ai" | "dual";
};

type JobSummary = {
  job_id: string;
  filename: string;
  status: "queued" | "processing" | "done" | "error" | "unknown" | "completed" | "failed" | "running";
  progress: number;
  ts: number;
  mode: string;
  organization_id?: string | null;
  organization_name?: string | null;
  organization_match_type?: string | null;
  organization_match_confidence?: number | null;
  structured_ingest_status?: string | null;
  structured_document_version_id?: number | null;
  structured_tables_count?: number | null;
  structured_recognized_tables?: number | null;
  structured_facts_count?: number | null;
  structured_document_profile?: string | null;
  structured_missing_optional_tables?: string[] | null;
  review_item_count?: number;
  low_confidence_item_count?: number;
  structured_report_id?: string | null;
  structured_table_data_count?: number | null;
  structured_line_item_count?: number | null;
  structured_sync_match_mode?: string | null;
  report_year?: number | null;
  report_kind?: "budget" | "final" | "unknown";
};

type CurrentUser = {
  username: string;
  is_admin: boolean;
};

const UPLOAD_REQUEST_TIMEOUT_MS = 180000;

export default function HomePage() {
  // Global State
  const [jobList, setJobList] = useState<JobSummary[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const jobListRef = useRef<JobSummary[]>([]);

  // Active Job UI State
  const [log, setLog] = useState<string[]>([]);
  const [job, setJob] = useState<UploadResp | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [url, setUrl] = useState("");
  const [uploadComplete, setUploadComplete] = useState(false);
  const [selectedIssue, setSelectedIssue] = useState<IssueItem | null>(null);

  // New State for QC V3
  const [qcFingings, setQcFindings] = useState<any[]>([]);
  const [isQcLoading, setIsQcLoading] = useState(false);
  const qcFindingsLengthRef = useRef(0);
  const isQcLoadingRef = useRef(false);

  // View mode for IssueTabs/List inside a job detail view
  const [issueViewMode, setIssueViewMode] = useState<"tabs" | "list" | "card">("tabs");
  const [showDebugLog, setShowDebugLog] = useState(false);

  // Config State
  const [aiAssistEnabled, setAiAssistEnabled] = useState<boolean | null>(null);
  const [useLocalRules, setUseLocalRules] = useState(true);
  const [useAiAssist, setUseAiAssist] = useState(true);

  // Polling
  const pollTimer = useRef<any>(null);
  const listPollTimer = useRef<any>(null);

  // Stuck Detection
  const [progressHistory, setProgressHistory] = useState<number[]>([]);
  const [showStuckWarning, setShowStuckWarning] = useState(false);

  // Organization State
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | null>(null);
  const [selectedDepartmentName, setSelectedDepartmentName] = useState<string>("");
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null); // selected unit id
  const [isAssociateOpen, setIsAssociateOpen] = useState(false);
  const [associatedJobId, setAssociatedJobId] = useState<string | null>(null);
  const [isGlobalReanalyzing, setIsGlobalReanalyzing] = useState(false);
  const [isReanalyzing, setIsReanalyzing] = useState(false);
  const [structuredCleanupBusyScope, setStructuredCleanupBusyScope] = useState<string | null>(null);
  const [structuredCleanupPreview, setStructuredCleanupPreview] = useState<StructuredCleanupPreviewPayload | null>(null);
  const [structuredCleanupScope, setStructuredCleanupScope] = useState<{
    departmentId: string | null;
    departmentName: string;
  }>({ departmentId: null, departmentName: "" });
  const [isStructuredCleanupExecuting, setIsStructuredCleanupExecuting] = useState(false);
  const [ignoringIssueId, setIgnoringIssueId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  useEffect(() => {
    let cancelled = false;

    const fetchCurrentUser = async () => {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setCurrentUser(null);
          }
          return;
        }
        const payload = await response.json().catch(() => ({}));
        if (!cancelled) {
          setCurrentUser((payload?.user ?? null) as CurrentUser | null);
        }
      } catch (error) {
        console.error("Failed to fetch current user", error);
        if (!cancelled) {
          setCurrentUser(null);
        }
      }
    };

    fetchCurrentUser();
    return () => {
      cancelled = true;
    };
  }, []);

  const isAdmin = Boolean(currentUser?.is_admin);


  // Manual Associate Suggestion State (from pendingAssociateJob logic)
  // This seems duplicate or legacy in original file, simplifying to one dialog state if possible
  // Keeping original logic for pendingAssociateJob if needed, but AssociateDialog usage suggests standard state.
  // We will assume `isAssociateOpen` is the primary one used by the new `OrganizationDetailView` flow?
  // Actually, let's keep the pendingAssociateJob one for "Automatic pop-up on failure" and `isAssociateOpen` for manual trigger.

  // Upload State
  // Upload State
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<any[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadDocType, setUploadDocType] = useState<"dept_final" | "dept_budget">("dept_final");
  const [isGlobalUploadOpen, setIsGlobalUploadOpen] = useState(false);

  // --- Layout State (New) ---
  const [viewMode, setViewMode] = useState<"org_detail" | "job_detail">("org_detail");
  const [orgRefreshKey, setOrgRefreshKey] = useState(0);
  const [orgTreeRefreshKey, setOrgTreeRefreshKey] = useState(0);
  const [showSidebarTools, setShowSidebarTools] = useState(false);
  const [orgImporterOpenSignal, setOrgImporterOpenSignal] = useState(0);

  useEffect(() => {
    qcFindingsLengthRef.current = qcFingings.length;
  }, [qcFingings.length]);

  useEffect(() => {
    jobListRef.current = jobList;
  }, [jobList]);

  useEffect(() => {
    isQcLoadingRef.current = isQcLoading;
  }, [isQcLoading]);

  function appendLog(s: string) {
    setLog((prev) => [...prev, s].slice(-200));
  }

  // 1. Fetch Config on Mount
  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const response = await fetch('/api/config', { cache: 'no-store' });
        const config = await response.json();
        const enabled = !!config.ai_assist_enabled && config.ai_extractor_alive === true;
        setAiAssistEnabled(enabled);
      } catch (error) {
        console.error('Failed to fetch config:', error);
        setAiAssistEnabled(false);
      }
    };
    fetchConfig();
  }, []);

  // 2. Job List Refresh
  const JOB_LIST_PAGE_SIZE = 200;
  const fetchJobList = useCallback(async () => {
    try {
      const res = await fetch(`/api/jobs?limit=${JOB_LIST_PAGE_SIZE}&offset=0`, { cache: 'no-store' });
      if (res.ok) {
        const payload = await res.json();
        const list = Array.isArray(payload)
          ? payload
          : Array.isArray(payload?.items)
            ? payload.items
            : [];
        setJobList(list);
      }
    } catch (e) {
      console.error("Failed to fetch job list", e);
    }
  }, []);

  useEffect(() => {
    if (!activeJobId) return;
    fetchJobList();
  }, [activeJobId, fetchJobList]);

  useEffect(() => {
    if (listPollTimer.current) {
      clearInterval(listPollTimer.current);
      listPollTimer.current = null;
    }

    if (!activeJobId) return;

    const activeStatus = status?.status;
    const listRefreshMs =
      activeStatus === "processing" || activeStatus === "running" || activeStatus === "queued"
        ? 15000
        : 45000;

    listPollTimer.current = setInterval(fetchJobList, listRefreshMs);
    return () => {
      if (listPollTimer.current) {
        clearInterval(listPollTimer.current);
        listPollTimer.current = null;
      }
    };
  }, [activeJobId, fetchJobList, status?.status]);

  // 3. Handle Active Job Switch
  const [isSwitching, setIsSwitching] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const hydrateActiveJob = async () => {
      if (!activeJobId) {
        setJob(null);
        setStatus(null);
        setLog([]);
        setIsSwitching(false);
        setQcFindings([]); // Reset QC findings
        setViewMode((prev) => (prev === 'org_detail' ? prev : 'org_detail'));
        return;
      }

      setViewMode((prev) => (prev === 'job_detail' ? prev : 'job_detail'));
      setIsSwitching(true);

      const selectedJob = jobListRef.current.find((j) => j.job_id === activeJobId);
      if (selectedJob) {
        setJob({
          job_id: selectedJob.job_id,
          filename: selectedJob.filename
        });
        // Initial status from list
        setStatus({
          job_id: selectedJob.job_id,
          status: (selectedJob.status as any),
          progress: selectedJob.progress,
          ts: selectedJob.ts
        });
        return;
      }

      try {
        const res = await fetch(`/api/jobs/${activeJobId}`, { cache: "no-store" });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const detail = await res.json();
        if (cancelled) return;

        setJob({
          job_id: activeJobId,
          filename: String(detail?.filename || ""),
        });
        setStatus({
          job_id: activeJobId,
          status: (detail?.status as any) || "unknown",
          progress: Number(detail?.progress ?? 0),
          ts: detail?.ts,
        } as any);
      } catch (e) {
        console.error("Failed to hydrate active job detail", e);
        if (cancelled) return;
        setJob({ job_id: activeJobId, filename: "" });
        setStatus({ job_id: activeJobId, status: "unknown" } as any);
      }
    };

    hydrateActiveJob();
    return () => {
      cancelled = true;
    };
  }, [activeJobId]);

  // 4. Poll Active Job Details
  useEffect(() => {
    if (!job?.job_id) return;

    let cancelled = false;
    let stableRounds = 0;
    let lastSnapshot = "";

    const schedulePoll = (delayMs: number) => {
      if (cancelled) return;
      if (pollTimer.current) {
        clearTimeout(pollTimer.current);
      }
      pollTimer.current = setTimeout(fetchStatus, delayMs);
    };

    const computeDelay = (payload: any): number | null => {
      const statusText = String(payload?.status || "unknown");
      if (["done", "completed", "error", "failed"].includes(statusText)) {
        return null;
      }

      const stageText = String(payload?.current_stage || payload?.stage || "");
      const progressValue =
        typeof payload?.progress === "number" ? payload.progress : -1;
      const snapshot = `${statusText}|${stageText}|${progressValue}`;

      if (snapshot === lastSnapshot) {
        stableRounds += 1;
      } else {
        stableRounds = 0;
        lastSnapshot = snapshot;
      }

      if (statusText === "queued") {
        return stableRounds >= 3 ? 8000 : 3500;
      }
      if (statusText === "processing" || statusText === "running") {
        if (stableRounds >= 6) return 5000;
        if (stableRounds >= 2) return 2500;
        return 1200;
      }
      return 5000;
    };

    const fetchStatus = async () => {
      try {
        const r = await fetch(`/api/jobs/${job.job_id}/status`, { cache: "no-store" });
        if (cancelled) return;

        // Note: The new /api/jobs/{id} returns different structure for V3 jobs (contains 'stages')
        // vs the old /api/jobs/{id}/status for V2 jobs.
        // We might need to handle both or assume the backend normalizes it.
        // Assuming we are using the new endpoint /api/jobs/{id} for both if possible, 
        // but let's stick to what's likely working or check the response.

        let js: any;
        if (r.status === 404) {
          // Fallback to old status endpoint if new one 404s (unlikely if same ID)
          // But actually, we should try the new endpoint first.
          // Let's assume the previous code used /api/jobs/{id}/status which was for V2.
          // If we want V3 details, we might need to hit /api/jobs/{id} directly.
          const r2 = await fetch(`/api/jobs/${job.job_id}`, { cache: "no-store" });
          js = await r2.json();
        } else {
          js = await r.json();
        }

        if (cancelled) return;

        setStatus(js);
        setIsSwitching(false);

        // Stuck Detection
        if ((js.status === "processing" || js.status === "running") && js.progress !== undefined) {
          setProgressHistory(prev => {
            // ... existing stuck logic
            return prev;
          });
        }

        // Fetch QC Findings if they exist and we haven't loaded them yet
        // Check for V3 structure
        const qcStage = js.stages?.qc;
        const runId = qcStage?.details?.run_id || js.qc_run_id;

        if (runId && qcFindingsLengthRef.current === 0 && !isQcLoadingRef.current) {
          setIsQcLoading(true);
          try {
            const fr = await fetch(`/api/qc/findings?run_id=${runId}`);
            if (fr.ok) {
              const findings = await fr.json();
              setQcFindings(findings);
            }
          } catch (e) {
            console.error("Failed to fetch QC findings", e);
          } finally {
            setIsQcLoading(false);
          }
        }

        const nextDelay = computeDelay(js);
        if (nextDelay !== null) {
          schedulePoll(nextDelay);
        } else if (pollTimer.current) {
          clearTimeout(pollTimer.current);
          pollTimer.current = null;
        }
      } catch (e: any) {
        console.error(e);
        if (!cancelled) setIsSwitching(false);
        schedulePoll(6000);
      }
    };

    fetchStatus();

    return () => {
      cancelled = true;
      if (pollTimer.current) {
        clearTimeout(pollTimer.current);
        pollTimer.current = null;
      }
    };
  }, [job?.job_id]);


  // Handlers for Views
  const handleOrgSelect = useCallback((org: any) => {
    setSelectedDepartmentId(org?.id || null);
    setSelectedDepartmentName(org?.name || "");
    setSelectedOrgId(org?.id || null);
    setViewMode("org_detail");
    setActiveJobId(null);
  }, []);

  const handleUnitSelect = useCallback((unit: any) => {
    setSelectedOrgId(unit?.id || null);
  }, []);

  const handleDepartmentDeleted = useCallback(() => {
    setSelectedDepartmentId(null);
    setSelectedDepartmentName("");
    setSelectedOrgId(null);
    setOrgRefreshKey((prev) => prev + 1);
    setOrgTreeRefreshKey((prev) => prev + 1);
  }, []);

  const openScopedUpload = useCallback((unit?: { id: string } | null) => {
    if (unit?.id) {
      setSelectedOrgId(unit.id);
    }
    setIsUploadOpen(true);
  }, []);

  const handleJobSelectFromOrg = (jobId: string) => {
    setActiveJobId(jobId);
    // viewMode effect will handle switching
  };

  const openAssociateDialog = useCallback((jobId: string) => {
    setAssociatedJobId(jobId);
    setIsAssociateOpen(true);
  }, []);

  const handleGlobalReanalyze = useCallback(async () => {
    if (!isAdmin) {
      setToast({ message: "仅管理员可以执行按部门重分析", type: "error" });
      return;
    }
    if (isGlobalReanalyzing) {
      return;
    }

    const confirmed = window.confirm(
      "\u5c06\u6309\u6bcf\u4e2a\u90e8\u95e8\u53ea\u53d6\u6700\u65b0\u4e00\u4efd\u62a5\u544a\u521b\u5efa\u65b0\u7684\u91cd\u5206\u6790\u4efb\u52a1\uff0c\u6b63\u5728\u5206\u6790\u4e2d\u7684\u4efb\u52a1\u4f1a\u81ea\u52a8\u8df3\u8fc7\u3002\u662f\u5426\u7ee7\u7eed\uff1f"
    );
    if (!confirmed) {
      return;
    }

    setIsGlobalReanalyzing(true);
    try {
      const response = await fetch("/api/jobs/reanalyze-all", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_active: false, latest_per_department: true }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message =
          String(payload?.detail || payload?.error || "").trim() || "\u6279\u91cf\u91cd\u5206\u6790\u5931\u8d25";
        throw new Error(message);
      }

      await fetchJobList().catch((listError) => {
        console.error("Failed to refresh job list after batch reanalyze", listError);
      });
      setOrgRefreshKey((prev) => prev + 1);
      setOrgTreeRefreshKey((prev) => prev + 1);

      const createdCount = Number(payload?.created_count ?? 0);
      const skippedCount = Number(payload?.skipped_count ?? 0);
      const failedCount = Number(payload?.failed_count ?? 0);
      const toastType =
        createdCount > 0 || failedCount === 0 ? "success" : "error";
      const message =
        createdCount > 0
          ? `\u5df2\u4e3a ${createdCount} \u4e2a\u90e8\u95e8\u7684\u6700\u65b0\u62a5\u544a\u521b\u5efa\u91cd\u5206\u6790\u4efb\u52a1\uff0c\u8df3\u8fc7 ${skippedCount} \u4e2a\uff0c\u5931\u8d25 ${failedCount} \u4e2a`
          : failedCount > 0
            ? `\u6279\u91cf\u91cd\u5206\u6790\u5931\u8d25 ${failedCount} \u4e2a\uff0c\u8df3\u8fc7 ${skippedCount} \u4e2a`
            : `\u6ca1\u6709\u53ef\u91cd\u5206\u6790\u7684\u90e8\u95e8\u6700\u65b0\u62a5\u544a\uff0c\u8df3\u8fc7 ${skippedCount} \u4e2a`;
      setToast({ message, type: toastType });
    } catch (error) {
      console.error("Failed to batch reanalyze jobs", error);
      setToast({
        message:
          error instanceof Error && error.message
            ? error.message
            : "\u6279\u91cf\u91cd\u5206\u6790\u5931\u8d25",
        type: "error",
      });
    } finally {
      setIsGlobalReanalyzing(false);
    }
  }, [fetchJobList, isAdmin, isGlobalReanalyzing]);

  const handleStructuredCleanup = useCallback(
    async (options?: { departmentId?: string | null; departmentName?: string | null }) => {
      if (!isAdmin) {
        setToast({ message: "仅管理员可以执行旧版结构化清理", type: "error" });
        return;
      }
      if (structuredCleanupBusyScope || isStructuredCleanupExecuting) {
        return;
      }

      const departmentId = String(options?.departmentId || "").trim();
      const departmentName = String(options?.departmentName || "").trim();
      const scopeKey = departmentId ? `department:${departmentId}` : "all";
      const requestBody = departmentId
        ? { dry_run: true, department_id: departmentId }
        : { dry_run: true };

      setStructuredCleanupBusyScope(scopeKey);
      try {
        const previewResponse = await fetch("/api/jobs/structured-ingest-cleanup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
        });
        const previewPayload = await previewResponse.json().catch(() => ({}));
        if (!previewResponse.ok) {
          const message =
            String(previewPayload?.detail || previewPayload?.error || "").trim() ||
            "旧版结构化入库预览失败";
          throw new Error(message);
        }

        setStructuredCleanupScope({
          departmentId: departmentId || null,
          departmentName,
        });
        setStructuredCleanupPreview(previewPayload as StructuredCleanupPreviewPayload);
      } catch (error) {
        console.error("Failed to cleanup structured ingest history", error);
        setToast({
          message:
            error instanceof Error && error.message
              ? error.message
              : "旧版结构化入库预览失败",
          type: "error",
        });
      } finally {
        setStructuredCleanupBusyScope(null);
      }
    },
    [isAdmin, isStructuredCleanupExecuting, structuredCleanupBusyScope]
  );

  const closeStructuredCleanupDialog = useCallback(() => {
    if (isStructuredCleanupExecuting) {
      return;
    }
    setStructuredCleanupPreview(null);
    setStructuredCleanupScope({ departmentId: null, departmentName: "" });
  }, [isStructuredCleanupExecuting]);

  const confirmStructuredCleanup = useCallback(async () => {
    if (!structuredCleanupPreview || isStructuredCleanupExecuting) {
      return;
    }

    const departmentId = structuredCleanupScope.departmentId;
    const scopeKey = departmentId ? `department:${departmentId}` : "all";
    const scopeLabel =
      structuredCleanupPreview.department_name ||
      structuredCleanupScope.departmentName ||
      (departmentId ? "当前部门" : "全库");

    setIsStructuredCleanupExecuting(true);
    setStructuredCleanupBusyScope(scopeKey);
    try {
      const executeBody = departmentId
        ? { dry_run: false, department_id: departmentId }
        : { dry_run: false };
      const executeResponse = await fetch("/api/jobs/structured-ingest-cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(executeBody),
      });
      const executePayload = await executeResponse.json().catch(() => ({}));
      if (!executeResponse.ok) {
        const message =
          String(executePayload?.detail || executePayload?.error || "").trim() ||
          "旧版结构化入库清理失败";
        throw new Error(message);
      }

      await fetchJobList().catch((listError) => {
        console.error("Failed to refresh job list after structured cleanup", listError);
      });
      setOrgRefreshKey((prev) => prev + 1);
      setOrgTreeRefreshKey((prev) => prev + 1);
      closeStructuredCleanupDialog();

      const deletedCount = Number(executePayload?.deleted_document_version_count ?? 0);
      const updatedCount = Number(executePayload?.updated_job_count ?? 0);
      setToast({
        message: `${scopeLabel}已清理 ${deletedCount} 个旧版入库版本，并更新 ${updatedCount} 条历史任务状态`,
        type: "success",
      });
    } catch (error) {
      console.error("Failed to cleanup structured ingest history", error);
      setToast({
        message:
          error instanceof Error && error.message
            ? error.message
            : "旧版结构化入库清理失败",
        type: "error",
      });
    } finally {
      setIsStructuredCleanupExecuting(false);
      setStructuredCleanupBusyScope(null);
    }
  }, [
    closeStructuredCleanupDialog,
    fetchJobList,
    isStructuredCleanupExecuting,
    structuredCleanupPreview,
    structuredCleanupScope,
  ]);

  const handleIgnoreIssue = useCallback(
    async (issue: IssueItem) => {
      const targetJobId =
        (typeof issue.job_id === "string" && issue.job_id.trim()) ||
        (typeof activeJobId === "string" && activeJobId.trim()) ||
        "";
      const targetIssueId = typeof issue.id === "string" ? issue.id.trim() : "";
      if (!targetJobId || !targetIssueId || ignoringIssueId === targetIssueId) {
        return;
      }

      setIgnoringIssueId(targetIssueId);
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(targetJobId)}/issues/ignore`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_id: targetIssueId }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const message =
            String(payload?.detail || payload?.error || "").trim() || "忽略问题失败";
          throw new Error(message);
        }

        if (activeJobId === targetJobId) {
          setStatus(payload as any);
        }
        setSelectedIssue((prev) => (prev?.id === targetIssueId ? null : prev));
        await fetchJobList().catch((listError) => {
          console.error("Failed to refresh job list after ignoring issue", listError);
        });
        setOrgRefreshKey((prev) => prev + 1);
        setToast({ message: "该来源命中已忽略，合并问题统计已刷新", type: "success" });
      } catch (error) {
        console.error("Failed to ignore issue", error);
        setToast({
          message:
            error instanceof Error && error.message
              ? error.message
              : "忽略问题失败",
          type: "error",
        });
      } finally {
        setIgnoringIssueId(null);
      }
    },
    [activeJobId, fetchJobList, ignoringIssueId]
  );

  const handleReanalyzeJob = useCallback(async () => {
    if (!activeJobId || isReanalyzing) {
      return;
    }

    setIsReanalyzing(true);
    try {
      const response = await fetch(`/api/jobs/${activeJobId}/reanalyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message =
          String(payload?.detail || payload?.error || "").trim() || "\u91cd\u65b0\u5206\u6790\u5931\u8d25";
        throw new Error(message);
      }

      const nextJobId =
        typeof payload?.job_id === "string" ? payload.job_id.trim() : "";
      if (!nextJobId) {
        throw new Error("\u672a\u8fd4\u56de\u65b0\u7684\u4efb\u52a1\u7f16\u53f7");
      }

      setActiveJobId(nextJobId);
      await fetchJobList().catch((listError) => {
        console.error("Failed to refresh job list after reanalyze", listError);
      });
      setToast({ message: "\u5df2\u521b\u5efa\u65b0\u7684\u91cd\u5206\u6790\u4efb\u52a1", type: "success" });
    } catch (error) {
      console.error("Failed to reanalyze job", error);
      setToast({
        message:
          error instanceof Error && error.message
            ? error.message
            : "\u91cd\u65b0\u5206\u6790\u5931\u8d25",
        type: "error",
      });
    } finally {
      setIsReanalyzing(false);
    }
  }, [activeJobId, fetchJobList, isReanalyzing]);

  const handleBackToOrg = () => {
    setActiveJobId(null); // This will trigger effect to switch to org_detail
  };

  // Upload Handlers (simplified)
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const files = Array.from(e.target.files);
      // Implement batch upload logic matching existing onPickFile
      // For now just taking first one or mocking loop
      try {
        for (const file of files) {
          const fd = new FormData();
          fd.set("file", file);
          // Perform upload...
          // This needs to be connected to the real upload logic
          // Reusing the simple onPickFile logic for single file for now or adapting it
          await onPickFile({ target: { files: [file] } } as any);
        }
      } catch (e) {
        console.error("Batch upload interrupted", e);
      } finally {
        setIsUploadOpen(false);
        e.target.value = ""; // Reset input to allow selecting same file again
      }
    }
  };

  const onPickFile = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const f = ev.target.files?.[0];
    if (!f) return;

    if (!selectedOrgId) {
      setToast({ message: "\u8bf7\u5148\u9009\u62e9\u90e8\u95e8\u540e\u518d\u4e0a\u4f20\u6587\u4ef6", type: "error" });
      return;
    }
    const detectUploadDocType = (filename: string): "dept_final" | "dept_budget" => {
      const lower = filename.toLowerCase();
      if (
        filename.includes("\u90e8\u95e8\u51b3\u7b97") ||
        lower.includes("final") ||
        lower.includes("settlement") ||
        lower.includes("accounts")
      ) {
        return "dept_final";
      }
      if (filename.includes("\u90e8\u95e8\u9884\u7b97") || lower.includes("budget")) {
        return "dept_budget";
      }
      return uploadDocType;
    };
    const detectFiscalYear = (filename: string): string => {
      const match4 = filename.match(/(20\d{2})/);
      if (match4) return match4[1];

      // Support file names like "...25?????.pdf" or "...24????.pdf".
      const match2 = filename.match(/(?:^|[^\d])(\d{2})(?=\s*(?:\u5e74)?(?:\u90e8\u95e8)?(?:\u51b3\u7b97|\u9884\u7b97))/);
      if (match2) {
        const year = Number(match2[1]);
        if (year >= 0 && year <= 99) {
          return String(2000 + year);
        }
      }

      // Let backend infer from content when filename has no reliable year token.
      return "";
    };
    const resolvedDocType = detectUploadDocType(f.name);
    const resolvedFiscalYear = detectFiscalYear(f.name);

    setIsUploading(true);
    setUploadProgress(0);

    const sendUpload = (url: string, formData: FormData) =>
      new Promise<{ status: number; responseText: string }>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        xhr.timeout = UPLOAD_REQUEST_TIMEOUT_MS;
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            const percent = Math.round((event.loaded / event.total) * 100);
            setUploadProgress(percent);
          }
        };
        xhr.onload = () => resolve({ status: xhr.status, responseText: xhr.responseText });
        xhr.onerror = () => reject(new Error("Network Error"));
        xhr.ontimeout = () => reject(new Error("Upload timed out"));
        xhr.send(formData);
      });

    const parseUploadError = (responseText: string, status: number) => {
      try {
        const payload = JSON.parse(responseText || "{}");
        return (
          payload?.detail ||
          payload?.error ||
          payload?.message ||
          responseText ||
          `HTTP ${status}`
        );
      } catch {
        return responseText || `HTTP ${status}`;
      }
    };

    try {
      const v3Form = new FormData();
      v3Form.set("file", f);
      v3Form.set("org_unit_id", selectedOrgId);
      if (resolvedFiscalYear) {
        v3Form.append("fiscal_year", resolvedFiscalYear);
      }
      v3Form.append("doc_type", resolvedDocType);

      let upload = await sendUpload("/api/documents/upload", v3Form);

      if (upload.status === 503) {
        setUploadProgress(0);
        const v2Form = new FormData();
        v2Form.set("file", f);
        v2Form.set("org_id", selectedOrgId);
        upload = await sendUpload("/api/upload", v2Form);
      }

      if (upload.status < 200 || upload.status >= 300) {
        throw new Error(parseUploadError(upload.responseText, upload.status));
      }

      setUploadProgress(100);

      let resp: any = {};
      try {
        resp = JSON.parse(upload.responseText || "{}");
      } catch (e) {
        console.error("Failed to parse upload response", e);
      }

      const versionId = resp?.id;
      if (versionId) {
        const runPayload: Record<string, unknown> = {
          mode: "dual",
          use_local_rules: useLocalRules,
          use_ai_assist: useAiAssist,
          doc_type: resolvedDocType,
        };
        if (resolvedFiscalYear) {
          runPayload.fiscal_year = resolvedFiscalYear;
          runPayload.report_year = Number(resolvedFiscalYear);
        }
        await fetch(`/api/documents/${versionId}/run`, {
          method: 'POST',
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(runPayload),
        });
      }

      if (selectedOrgId) {
        setOrgRefreshKey(prev => prev + 1);
      }

      fetchJobList().catch(console.error);

      if (selectedOrgId) {
        const refreshWithRetry = async () => {
          const delays = [300, 800, 2000];
          for (const delay of delays) {
            await new Promise(r => setTimeout(r, delay));
            setOrgRefreshKey(prev => prev + 1);
          }
        };
        refreshWithRetry();
      }

      const typeLabel = resolvedDocType === "dept_budget" ? "\u90e8\u95e8\u9884\u7b97\u62a5\u544a" : "\u90e8\u95e8\u51b3\u7b97\u62a5\u544a";
      setToast({ message: `\u5df2\u4e0a\u4f20 ${resolvedFiscalYear ? `${resolvedFiscalYear}\u5e74` : ""}${typeLabel}\uff0c\u5df2\u5f00\u59cb\u5206\u6790`, type: "success" });
    } catch (e: any) {
      console.error("Upload failed", e);
      setToast({ message: e?.message || "\u6587\u4ef6\u4e0a\u4f20\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5", type: "error" });
    } finally {
      setIsUploading(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(false); };
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setIsDragging(false);
    if (e.dataTransfer.files) {
      // Handle drop upload
      const files = Array.from(e.dataTransfer.files);
      for (const file of files) {
        await onPickFile({ target: { files: [file] } } as any);
      }
      setIsUploadOpen(false);
    }
  };

  // Issue Helpers (normalizeIssues, isDualMode, etc) ... 
  // Copying essential logic for IssueTabs
  interface IssuesBuckets { error: Issue[]; warn: Issue[]; info: Issue[]; all: Issue[]; }
  function normalizeIssues(raw: any): IssuesBuckets {
    const empty: IssuesBuckets = { error: [], warn: [], info: [], all: [] };
    if (!raw) return empty;
    const toBucket = (severity: any): "error" | "warn" | "info" => {
      const s = String(severity || "").toLowerCase();
      if (["critical", "high", "error", "err", "fatal"].includes(s)) return "error";
      if (["warn", "warning", "medium", "low"].includes(s)) return "warn";
      return "info";
    };

    if (Array.isArray(raw)) {
      const buckets: IssuesBuckets = { error: [], warn: [], info: [], all: [] };
      for (const item of raw) {
        const bucket = toBucket(item?.severity);
        buckets[bucket].push(item);
        buckets.all.push(item);
      }
      return buckets;
    }

    if (typeof raw === "object") {
      const error = Array.isArray(raw.error) ? raw.error : [];
      const warn = Array.isArray(raw.warn) ? raw.warn : [];
      const info = Array.isArray(raw.info) ? raw.info : [];
      const all = Array.isArray(raw.all) ? raw.all : [...error, ...warn, ...info];
      return { error, warn, info, all };
    }

    return empty;
  }

  // Re-implementing helper logic inside render or using the ones from state
  // ideally these should be outside or memoized.

  // Let's assume buckets/allIssues/findings are derived from `status`
  const buckets = normalizeIssues((status as any)?.result?.issues);
  // ... (rest of logic)

  // Derived data for display
  const jobStatus = status?.status;
  const isPolling = jobStatus === 'processing' || jobStatus === 'queued' || jobStatus === "running";
  const activeJobSummary = jobList.find((item) => item.job_id === activeJobId) || null;
  const activeStatusAny = status as any;
  const activeOrganizationName =
    activeStatusAny?.organization_name ||
    activeJobSummary?.organization_name ||
    null;
  const activeOrganizationMatchType =
    activeStatusAny?.organization_match_type ||
    activeJobSummary?.organization_match_type ||
    null;
  const activeOrganizationMatchConfidence =
    typeof activeStatusAny?.organization_match_confidence === "number"
      ? activeStatusAny.organization_match_confidence
      : typeof activeJobSummary?.organization_match_confidence === "number"
        ? activeJobSummary.organization_match_confidence
        : null;
  const structuredIngestStatus =
    activeStatusAny?.structured_ingest?.status ||
    activeStatusAny?.structured_ingest_status ||
    activeJobSummary?.structured_ingest_status ||
    null;
  const activeStructuredIngest: StructuredIngestPayload | null =
    activeStatusAny?.structured_ingest && typeof activeStatusAny.structured_ingest === "object"
      ? activeStatusAny.structured_ingest
      : activeJobSummary
        ? {
          status: activeJobSummary.structured_ingest_status,
          document_version_id: activeJobSummary.structured_document_version_id,
          tables_count: activeJobSummary.structured_tables_count,
          recognized_tables: activeJobSummary.structured_recognized_tables,
          facts_count: activeJobSummary.structured_facts_count,
          document_profile: activeJobSummary.structured_document_profile,
          missing_optional_tables: activeJobSummary.structured_missing_optional_tables,
          review_item_count: activeJobSummary.review_item_count,
          low_confidence_item_count: activeJobSummary.low_confidence_item_count,
          ps_sync: {
            report_id: activeJobSummary.structured_report_id,
            table_data_count: activeJobSummary.structured_table_data_count,
            line_item_count: activeJobSummary.structured_line_item_count,
            match_mode: activeJobSummary.structured_sync_match_mode,
          },
        }
        : null;
  const structuredReviewCount =
    Number(
      activeStatusAny?.structured_ingest?.review_item_count ??
      activeStatusAny?.review_item_count ??
      activeJobSummary?.review_item_count ??
      0
    ) || 0;
  const structuredLowConfidenceCount =
    Number(
      activeStatusAny?.structured_ingest?.low_confidence_item_count ??
      activeStatusAny?.low_confidence_item_count ??
      activeJobSummary?.low_confidence_item_count ??
      0
    ) || 0;
  const structuredRecognizedTables =
    Number(
      activeStructuredIngest?.recognized_tables ??
      activeJobSummary?.structured_recognized_tables ??
      0
    ) || 0;
  const structuredTablesCount =
    Number(
      activeStructuredIngest?.tables_count ??
      activeJobSummary?.structured_tables_count ??
      0
    ) || 0;
  const structuredFactsCount =
    Number(
      activeStructuredIngest?.facts_count ??
      activeJobSummary?.structured_facts_count ??
      0
    ) || 0;
  const structuredLineItemCount =
    Number(
      activeStructuredIngest?.ps_sync?.line_item_count ??
      activeJobSummary?.structured_line_item_count ??
      0
    ) || 0;

  // Mock data for UI if status not full
  const aiFindings = (status as any)?.result?.ai_findings || (status as any)?.ai_findings || [];
  const legacyRuleFindings: IssueItem[] = buckets.all.map((item: any, idx: number) => {
    const rawSeverity = String(item?.severity || "").toLowerCase();
    const severityMap: Record<string, IssueItem["severity"]> = {
      error: "high",
      high: "high",
      critical: "critical",
      warn: "medium",
      warning: "medium",
      medium: "medium",
      low: "low",
      info: "info",
    };
    return {
      id: String(item?.id || `${item?.rule_id || item?.rule || "RULE"}-${idx}`),
      source: "rule",
      rule_id: item?.rule_id || item?.rule || "",
      severity: severityMap[rawSeverity] || "medium",
      title: item?.title || item?.message || item?.rule_id || item?.rule || "\u672a\u547d\u540d\u95ee\u9898",
      message: item?.message || item?.title || "",
      evidence: Array.isArray(item?.evidence)
        ? item.evidence
        : item?.evidence
          ? [item.evidence]
          : [],
      location: (item?.location && typeof item.location === "object") ? item.location : {},
      metrics: (item?.metrics && typeof item.metrics === "object") ? item.metrics : {},
      tags: Array.isArray(item?.tags) ? item.tags : [],
      created_at: item?.created_at || Date.now() / 1000,
      job_id: activeJobId || undefined,
    };
  });
  const ruleFindingsRaw = (status as any)?.result?.rule_findings || (status as any)?.rule_findings || [];
  const ruleFindings = Array.isArray(ruleFindingsRaw) && ruleFindingsRaw.length > 0
    ? ruleFindingsRaw
    : legacyRuleFindings;
  const mergedFindings = (status as any)?.result?.merged || (status as any)?.merged;
  const enrichedIssues = buckets.all.map(i => ({ ...i, source: i.source || 'rule' }));

  const consistencyPairs = (status as any)?.result?.dual_mode?.consistency_pairs || [];
  const conflictPairs = (status as any)?.result?.dual_mode?.conflict_pairs || [];

  const [activeTab, setActiveTab] = useState<"ai" | "rule" | "merged">("merged");

  const jumpToPage = (page: number) => {
    // implementation
  };

  // --- Render ---
  const [showOrgTree, setShowOrgTree] = useState(true);

  return (
    <div className="flex h-screen bg-[#F5F5FA] dark:bg-gray-900 overflow-hidden font-sans selection:bg-indigo-500/30">

      {/* Background Ambient Glows */}
      <div className="fixed top-0 left-0 w-full h-full overflow-hidden pointer-events-none z-0">
        <div className="absolute top-[-20%] left-[-10%] w-[50%] h-[50%] bg-blue-400/10 rounded-full blur-[120px] dark:bg-blue-900/20"></div>
        <div className="absolute bottom-[-20%] right-[-10%] w-[50%] h-[50%] bg-purple-400/10 rounded-full blur-[120px] dark:bg-purple-900/20"></div>
      </div>

      {/* Sidebar */}
      <div
        className={`${showOrgTree ? 'w-80' : 'w-0'} transition-all duration-300 ease-[cubic-bezier(0.25,0.1,0.25,1)] relative z-10 border-r border-gray-200/50 dark:border-gray-700/50 bg-white/70 dark:bg-gray-800/70 backdrop-blur-xl flex flex-col overflow-hidden`}
      >
        <div className="min-w-[320px] h-full flex flex-col pt-4 relative">
          <div className="px-6 mb-6">
            <div className="flex items-center space-x-2">
              <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg shadow-lg shadow-indigo-500/30 flex items-center justify-center text-white font-bold">G</div>
              <span className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-gray-900 to-gray-600 dark:from-white dark:to-gray-400">GovChecker</span>
            </div>
            <div className="mt-4">
              <ActionAccordion
                title="快捷操作"
                description="默认收起，按需查阅批量重跑或清理"
                isOpen={showSidebarTools}
                onToggle={() => setShowSidebarTools((prev) => !prev)}
                icon={<svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>}
              >
                <div>
                  <button
                    onClick={handleGlobalReanalyze}
                    disabled={!isAdmin || isGlobalReanalyzing}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-orange-50 px-4 py-2.5 text-sm font-semibold text-amber-700 shadow-sm transition-all hover:border-amber-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title={isAdmin ? "按每个部门的最新报告批量创建新的重分析任务" : "仅管理员可操作"}
                  >
                    {isGlobalReanalyzing ? "批量重分析中..." : "按部门重分析"}
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-amber-700/80">
                    {isAdmin ? "每个部门只重跑最新一份报告，默认跳过正在分析中的任务。" : "该操作仅管理员可用。"}
                  </p>
                </div>
                <div>
                  <button
                    type="button"
                    onClick={() => setIsGlobalUploadOpen(true)}
                    disabled={!isAdmin}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-indigo-200 bg-gradient-to-r from-indigo-50 to-blue-50 px-4 py-2.5 text-sm font-semibold text-indigo-700 shadow-sm transition-all hover:border-indigo-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title={isAdmin ? "上传全区 PDF 文档（批量）" : "仅管理员可操作"}
                  >
                    全区上传
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-indigo-700/80">
                    {isAdmin ? "批量上传全区 PDF 报告，后台将自动创建任务并刷新组织统计。" : "全区批量上传仅管理员可用。"}
                  </p>
                </div>
                <div>
                  <button
                    type="button"
                    onClick={() => setOrgImporterOpenSignal((prev) => prev + 1)}
                    disabled={!isAdmin}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-emerald-200 bg-gradient-to-r from-emerald-50 to-teal-50 px-4 py-2.5 text-sm font-semibold text-emerald-700 shadow-sm transition-all hover:border-emerald-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title={isAdmin ? "导入部门及单位名称模板（CSV / XLSX）" : "仅管理员可操作"}
                  >
                    导入部门名称
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-emerald-700/80">
                    {isAdmin ? "导入部门和单位名称模板，快速初始化或批量更新组织结构。" : "组织导入仅管理员可用。"}
                  </p>
                </div>
                <div>
                  <button
                    onClick={() => handleStructuredCleanup()}
                    disabled={!isAdmin || !!structuredCleanupBusyScope || isStructuredCleanupExecuting}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-sky-200 bg-gradient-to-r from-sky-50 to-cyan-50 px-4 py-2.5 text-sm font-semibold text-sky-700 shadow-sm transition-all hover:border-sky-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title={isAdmin ? "清理数据库中的旧版记录，前台合并问题不变" : "仅管理员可操作"}
                  >
                    {structuredCleanupBusyScope === "all"
                      ? (isStructuredCleanupExecuting ? "清理中..." : "加载预览...")
                      : "清理旧版入库"}
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-sky-700/80">
                    {isAdmin ? "清理旧记录，不删除原始报告和前台的合并问题记录。" : "旧版清理仅管理员可用。"}
                  </p>
                </div>
              </ActionAccordion>
            </div>
          </div>
          <OrganizationTree
            onSelect={handleOrgSelect}
            onGlobalBatchUpload={() => setIsGlobalUploadOpen(true)}
            hideUtilityActions={true}
            openImporterSignal={orgImporterOpenSignal}
            isAdmin={isAdmin}
            selectedOrgId={selectedDepartmentId}
            refreshKey={orgTreeRefreshKey}
          />

          {/* Bottom Toggle Button */}
          {showOrgTree && (
            <div className="absolute bottom-4 right-4 z-20">
              <button
                onClick={() => setShowOrgTree(false)}
                className="p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm text-gray-400 hover:text-indigo-600 hover:shadow-md transition-all duration-200"
                title="\u6536\u8d77\u90e8\u95e8\u6811"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" /></svg>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Toggle Buttons */}
      {!showOrgTree && (
        <div className="absolute left-4 top-4 z-50">
          <button onClick={() => setShowOrgTree(true)} className="p-2 bg-white/80 dark:bg-gray-800/80 backdrop-blur-md border border-gray-200/50 dark:border-gray-700/50 rounded-xl shadow-lg hover:shadow-xl transition-all duration-200 text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
        </div>
      )}

      {/* Main Area */}
      <div className="flex-1 flex flex-col h-full overflow-hidden relative z-10">

        {viewMode === 'org_detail' && (
          selectedDepartmentId ? (
            <div className="flex-1 overflow-hidden animate-in fade-in slide-in-from-bottom-4 duration-500">
              <OrganizationDetailView
                departmentId={selectedDepartmentId}
                departmentName={selectedDepartmentName}
                isAdmin={isAdmin}
                selectedUnitId={selectedOrgId}
                onSelectUnit={handleUnitSelect}
                onSelectJob={handleJobSelectFromOrg}
                onUpload={openScopedUpload}
                onCleanupStructuredHistory={handleStructuredCleanup}
                isCleaningStructuredHistory={!!structuredCleanupBusyScope || isStructuredCleanupExecuting}
                refreshKey={orgRefreshKey}
                onJobDeleted={() => setOrgTreeRefreshKey(prev => prev + 1)}
                onUnitCreated={() => setOrgTreeRefreshKey(prev => prev + 1)}
                onUnitDeleted={() => setOrgTreeRefreshKey(prev => prev + 1)}
                onDepartmentDeleted={handleDepartmentDeleted}
              />
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-10 animate-in zoom-in-95 duration-500">
              <div className="w-24 h-24 bg-gradient-to-tr from-gray-100 to-gray-200 dark:from-gray-800 dark:to-gray-700 rounded-3xl flex items-center justify-center mb-6 shadow-inner">
                <svg className="w-12 h-12 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" /></svg>
              </div>
              <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">{"\u8bf7\u5148\u4ece\u5de6\u4fa7\u9009\u62e9\u4e00\u4e2a\u90e8\u95e8"}</h2>
              <p className="text-gray-500 dark:text-gray-400 max-w-md">{"\u9009\u4e2d\u90e8\u95e8\u540e\u53ef\u67e5\u770b\u6700\u65b0\u62a5\u544a\u3001\u95ee\u9898\u5217\u8868\u3001\u5165\u5e93\u72b6\u6001\u548c\u91cd\u5206\u6790\u5165\u53e3\u3002"}</p>
            </div>
          )
        )}

        {viewMode === 'job_detail' && (
          <div className="flex-1 flex flex-col overflow-hidden bg-white/50 dark:bg-gray-800/50 backdrop-blur-xl animate-in slide-in-from-right-8 duration-500">
            <div className="border-b border-gray-200/50 dark:border-gray-700/50 px-6 py-4 flex-shrink-0 bg-white/40 dark:bg-gray-900/40 backdrop-blur-md">
              <div className="flex items-center flex-wrap gap-y-2">
                <button onClick={handleBackToOrg} className="mr-4 p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 hover:text-gray-900 transition-colors group">
                  <div className="flex items-center space-x-1">
                    <svg className="w-5 h-5 transform group-hover:-translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" /></svg>
                    <span className="text-sm font-medium">{"\u8fd4\u56de\u90e8\u95e8\u89c6\u56fe"}</span>
                  </div>
                </button>
                <div className="h-6 w-px bg-gray-300 dark:bg-gray-700 mx-2"></div>
                <h2 className="ml-2 text-lg font-bold text-gray-800 dark:text-white truncate max-w-lg">
                  {job?.filename || jobList.find(j => j.job_id === activeJobId)?.filename || "\u672a\u547d\u540d\u6587\u4ef6..."}
                </h2>
                <span className={`ml-3 px-2 py-0.5 rounded text-xs font-medium ${jobStatus === 'done' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}`}>
                  {jobStatus === 'done' ? "\u5206\u6790\u5b8c\u6210" : jobStatus === 'processing' ? "\u5206\u6790\u4e2d" : "\u5f85\u5904\u7406"}
                </span>
                {activeJobId && (
                  <button
                    onClick={handleReanalyzeJob}
                    disabled={isReanalyzing}
                    className="ml-3 inline-flex items-center rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isReanalyzing ? "\u91cd\u65b0\u5206\u6790\u4e2d..." : "\u91cd\u65b0\u5206\u6790"}
                  </button>
                )}
                {activeJobId && (
                  <button
                    onClick={() => openAssociateDialog(activeJobId)}
                    className="ml-3 inline-flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-100"
                  >
                    {activeOrganizationName ? "\u91cd\u65b0\u5173\u8054\u90e8\u95e8" : "\u5173\u8054\u90e8\u95e8"}
                  </button>
                )}
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${activeOrganizationName ? "bg-slate-100 text-slate-700" : "bg-amber-100 text-amber-700"}`}>
                  {activeOrganizationName ? `\u5f53\u524d\u5173\u8054\u90e8\u95e8\uff1a${activeOrganizationName}` : "\u5c1a\u672a\u5173\u8054\u90e8\u95e8"}
                </span>
                {activeOrganizationMatchType && (
                  <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${activeOrganizationMatchType === "manual" ? "bg-purple-100 text-purple-700" : "bg-blue-100 text-blue-700"}`}>
                    {activeOrganizationMatchType === "manual" ? "\u624b\u52a8\u5173\u8054" : "\u81ea\u52a8\u5339\u914d"}
                  </span>
                )}
                {typeof activeOrganizationMatchConfidence === "number" && activeOrganizationMatchConfidence > 0 && (
                  <span className="inline-flex items-center rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
                    {"\u5339\u914d\u7f6e\u4fe1\u5ea6\uff1a"}{(activeOrganizationMatchConfidence * 100).toFixed(0)}%
                  </span>
                )}
                <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${structuredIngestStatus === "done" ? "bg-emerald-100 text-emerald-700" : structuredIngestStatus === "error" ? "bg-red-100 text-red-700" : structuredIngestStatus === "skipped" ? "bg-gray-100 text-gray-700" : "bg-amber-100 text-amber-700"}`}>
                  {structuredIngestStatus === "done"
                    ? "\u7ed3\u6784\u5316\u5165\u5e93\uff1a\u5b8c\u6210"
                    : structuredIngestStatus === "error"
                      ? "\u7ed3\u6784\u5316\u5165\u5e93\uff1a\u5931\u8d25"
                      : structuredIngestStatus === "skipped"
                        ? "\u7ed3\u6784\u5316\u5165\u5e93\uff1a\u5df2\u8df3\u8fc7"
                        : "\u7ed3\u6784\u5316\u5165\u5e93\uff1a\u8fdb\u884c\u4e2d"}
                </span>
                {structuredReviewCount > 0 && (
                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">
                    {"\u5f85\u590d\u6838\u9879\uff1a"}{structuredReviewCount}
                  </span>
                )}
                {structuredLowConfidenceCount > 0 && (
                  <span className="inline-flex items-center rounded-full bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700">
                    {"\u4f4e\u7f6e\u4fe1\u5ea6\uff1a"}{structuredLowConfidenceCount}
                  </span>
                )}
                {structuredRecognizedTables > 0 && (
                  <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">
                    {"\u5df2\u8bc6\u522b\u8868\u683c\uff1a"}{structuredRecognizedTables}{structuredTablesCount > 0 ? `/${structuredTablesCount}` : ""}
                  </span>
                )}
                {structuredFactsCount > 0 && (
                  <span className="inline-flex items-center rounded-full bg-sky-100 px-2.5 py-1 text-xs font-medium text-sky-700">
                    facts {structuredFactsCount}
                  </span>
                )}
                {structuredLineItemCount > 0 && (
                  <span className="inline-flex items-center rounded-full bg-teal-100 px-2.5 py-1 text-xs font-medium text-teal-700">
                    {"PS \u884c\u9879\uff1a"}{structuredLineItemCount}
                  </span>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-hidden flex flex-col p-6">
              {activeJobId ? (
                <div className="flex-1 flex flex-col h-full overflow-hidden">
                  {(status as any)?.stages ? (
                    /* V3 Pipeline View */
                    <div className="flex-1 overflow-y-auto pr-2 pb-20 custom-scrollbar space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
                      {/* Pipeline Status */}
                      <div className="bg-white/60 dark:bg-gray-800/60 backdrop-blur-md rounded-2xl border border-white/20 dark:border-gray-700/50 shadow-sm p-4">
                        <PipelineStatus stages={(status as any).stages} currentStage={(status as any).current_stage} />
                      </div>

                      <StructuredIngestPanel payload={activeStructuredIngest} />

                      {/* QC Results */}
                      <div>
                        <div className="flex items-center justify-between mb-4 px-2">
                          <h3 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-gray-900 to-indigo-600 dark:from-white dark:to-indigo-400 flex items-center">
                            <span className="mr-2 text-2xl">#</span> {"\u95ee\u9898\u68c0\u67e5\u7ed3\u679c"}
                          </h3>
                          {(status as any)?.report_path && (
                            <div className="flex items-center gap-2 flex-wrap">
                              <a
                                href={`/api/reports/download?job_id=${activeJobId}`}
                                target="_blank"
                                className="inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium shadow-md hover:shadow-lg transition-all"
                              >
                                <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
                                {"\u4e0b\u8f7d PDF \u62a5\u544a"}
                              </a>
                              <a
                                href={`/api/reports/download?job_id=${activeJobId}&format=csv`}
                                target="_blank"
                                className="inline-flex items-center px-3 py-2 bg-white hover:bg-slate-50 text-slate-700 rounded-lg text-sm font-medium border border-slate-200 shadow-sm transition-all"
                              >
                                {"\u5bfc\u51fa CSV"}
                              </a>
                              <a
                                href={`/api/reports/download?job_id=${activeJobId}&format=json`}
                                target="_blank"
                                className="inline-flex items-center px-3 py-2 bg-white hover:bg-slate-50 text-slate-700 rounded-lg text-sm font-medium border border-slate-200 shadow-sm transition-all"
                              >
                                {"\u5bfc\u51fa JSON"}
                              </a>
                            </div>
                          )}
                        </div>

                        <div className="bg-white/40 dark:bg-gray-800/40 backdrop-blur-sm rounded-2xl p-6 border border-white/20 dark:border-gray-700/30">
                          <QCResultView findings={qcFingings} isLoading={isQcLoading} />
                        </div>
                      </div>
                    </div>
                  ) : (
                    /* V2 Legacy View */
                    <div className="flex-1 overflow-y-auto pr-2 pb-20 custom-scrollbar">
                      <div className="mb-6">
                        <StructuredIngestPanel payload={activeStructuredIngest} />
                      </div>

                      {/* Stats Cards */}
                      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                        <div className="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-between">
                          <div>
                            <p className="text-gray-500 dark:text-gray-400 text-xs font-medium uppercase tracking-wider">{"\u95ee\u9898\u603b\u6570"}</p>
                            <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">{enrichedIssues.length}</p>
                          </div>
                          <div className="p-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 rounded-lg">
                            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                          </div>
                        </div>
                        <div className="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-between">
                          <div>
                            <p className="text-gray-500 dark:text-gray-400 text-xs font-medium uppercase tracking-wider">{"\u4e00\u81f4\u6027\u914d\u5bf9"}</p>
                            <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">{consistencyPairs.length}</p>
                          </div>
                          <div className="p-2 bg-green-50 dark:bg-green-900/20 text-green-600 rounded-lg">
                            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                          </div>
                        </div>
                        <div className="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-between">
                          <div>
                            <p className="text-gray-500 dark:text-gray-400 text-xs font-medium uppercase tracking-wider">{"\u51b2\u7a81\u9879"}</p>
                            <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">{conflictPairs.length}</p>
                          </div>
                          <div className="p-2 bg-red-50 dark:bg-red-900/20 text-red-600 rounded-lg">
                            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                          </div>
                        </div>
                        <div className="bg-white dark:bg-gray-800 p-4 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm flex items-center justify-between">
                          <div>
                            <p className="text-gray-500 dark:text-gray-400 text-xs font-medium uppercase tracking-wider">{"AI \u95ee\u9898\u6570"}</p>
                            <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">{enrichedIssues.filter(i => i.source === 'ai').length}</p>
                          </div>
                          <div className="p-2 bg-yellow-50 dark:bg-yellow-900/20 text-yellow-600 rounded-lg">
                            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                          </div>
                        </div>
                      </div>

                      {/* Issues Tabs */}
                      <IssueTabs
                        result={{
                          ai_findings: aiFindings,
                          rule_findings: ruleFindings,
                          merged: mergedFindings || {
                            totals: {
                              ai: Array.isArray(aiFindings) ? aiFindings.length : 0,
                              rule: Array.isArray(ruleFindings) ? ruleFindings.length : 0,
                              merged: Array.isArray(aiFindings) ? aiFindings.length + (Array.isArray(ruleFindings) ? ruleFindings.length : 0) : (Array.isArray(ruleFindings) ? ruleFindings.length : 0),
                              conflicts: 0,
                              agreements: 0
                            },
                            conflicts: [],
                            agreements: []
                          },
                          meta: (status as any)?.result?.meta || {}
                        }}
                        job_id={activeJobId || undefined}
                        onIssueClick={setSelectedIssue}
                        onIgnoreIssue={handleIgnoreIssue}
                        ignoringIssueId={ignoringIssueId}
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center flex-1 h-full">
                  <div className="w-16 h-16 border-4 border-gray-200 border-t-indigo-500 rounded-full animate-spin"></div>
                  <p className="mt-4 text-gray-500">{"\u6b63\u5728\u52a0\u8f7d\u5206\u6790\u7ed3\u679c\uff0c\u8bf7\u7a0d\u5019..."}</p>
                </div>
              )}
            </div>

          </div>
        )}
      </div>

      {/* Batch Upload Modal (闂傚倷娴囬褏鎹㈤幒妤€纾婚柟鐗堟緲绾惧鏌熼崜褏甯涢柍閿嬪浮閺屾盯寮撮妸銉ヮ潻婵犳鍠栭崯鎵閹烘せ鈧箓骞嬪┑鍡忔嫬闁诲孩顔栭崰鏍€﹂悜鐣屽祦闁搞儺鍓﹂弫鍡涙煃瑜滈崜娑氬垝閸懇鍋撻敐搴樺亾? */}
      {isUploadOpen && selectedOrgId && (
        <BatchUploadModal
          orgUnitId={selectedOrgId}
          defaultDocType={uploadDocType}
          useLocalRules={useLocalRules}
          useAiAssist={useAiAssist}
          onClose={() => setIsUploadOpen(false)}
          onComplete={() => {
            fetchJobList().catch(console.error);
            if (selectedOrgId) {
              setOrgRefreshKey(prev => prev + 1);
              const refreshWithRetry = async () => {
                const delays = [300, 800, 2000];
                for (const delay of delays) {
                  await new Promise(r => setTimeout(r, delay));
                  setOrgRefreshKey(prev => prev + 1);
                }
              };
              refreshWithRetry();
            }
            setToast({ message: "浠诲姟瀹屾垚锛屽垪琛ㄥ凡鑷姩鍒锋柊", type: "success" });
          }}
        />
      )}

      {/* Global Batch Upload Modal (闂傚倸鍊烽懗鍫曗€﹂崼銏″床闁割偁鍎辩粈澶屸偓鍏夊亾闁逞屽墯缁傚秹骞栨笟鍥ㄦ櫖濠殿噯缍€閸嬫劙宕伴弽顓炲瀭闁诡垎鍛闂佹悶鍎弲鈺呭绩? */}
      {isGlobalUploadOpen && (
        <BatchUploadModal
          defaultDocType={uploadDocType}
          useLocalRules={useLocalRules}
          useAiAssist={useAiAssist}
          onClose={() => setIsGlobalUploadOpen(false)}
          onComplete={() => {
            fetchJobList().catch(console.error);
            setOrgRefreshKey(prev => prev + 1);
            setOrgTreeRefreshKey(prev => prev + 1);
            const refreshWithRetry = async () => {
              const delays = [300, 800, 2000];
              for (const delay of delays) {
                await new Promise(r => setTimeout(r, delay));
                setOrgRefreshKey(prev => prev + 1);
                setOrgTreeRefreshKey(prev => prev + 1);
              }
            };
            refreshWithRetry();
            setToast({ message: "\u6279\u91cf\u4e0a\u4f20\u4efb\u52a1\u5df2\u63d0\u4ea4\uff0c\u7cfb\u7edf\u6b63\u5728\u540e\u53f0\u5206\u6790", type: "success" });
          }}
        />
      )}

      {/* Associate Dialog */}
      <AssociateDialog
        isOpen={isAssociateOpen}
        onClose={() => setIsAssociateOpen(false)}
        jobId={associatedJobId || ''}
        filename={jobList.find(j => j.job_id === associatedJobId)?.filename || job?.filename || "\u672a\u547d\u540d\u6587\u4ef6"}
        onAssociate={async (orgId) => {
          try {
            if (associatedJobId) {
              const response = await fetch(`/api/jobs/${associatedJobId}/associate`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ org_id: orgId })
              });
              if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
              }
              fetchJobList();
              if (activeJobId === associatedJobId) {
                const detailResponse = await fetch(`/api/jobs/${associatedJobId}`, { cache: "no-store" });
                if (detailResponse.ok) {
                  const detail = await detailResponse.json();
                  setJob({
                    job_id: associatedJobId,
                    filename: String(detail?.filename || job?.filename || ""),
                    organization_id: detail?.organization_id,
                    organization_name: detail?.organization_name,
                    organization_match_type: detail?.organization_match_type,
                    organization_match_confidence: detail?.organization_match_confidence,
                  });
                  setStatus(detail);
                }
              }
              setOrgRefreshKey(prev => prev + 1);
              setIsAssociateOpen(false);
              setToast({ message: "\u90e8\u95e8\u5173\u8054\u5df2\u66f4\u65b0", type: "success" });
            }
          } catch (e) {
            console.error(e);
            setToast({ message: "\u90e8\u95e8\u5173\u8054\u66f4\u65b0\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5", type: "error" });
          }
        }}
      />
      <StructuredCleanupDialog
        isOpen={!!structuredCleanupPreview}
        preview={structuredCleanupPreview}
        isExecuting={isStructuredCleanupExecuting}
        onClose={closeStructuredCleanupDialog}
        onConfirm={confirmStructuredCleanup}
      />
      {/* Toast Notification */}
      {toast && (
        <div className="fixed top-6 left-1/2 transform -translate-x-1/2 z-[200] animate-in slide-in-from-top-4 fade-in duration-300">
          <div className="bg-white dark:bg-gray-800 rounded-full shadow-2xl border border-green-200 dark:border-green-800/30 px-6 py-3 flex items-center space-x-3">
            <div className={`p-1 rounded-full ${toast.type === 'success' ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'}`}>
              {toast.type === 'success' ? (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
              ) : (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
              )}
            </div>
            <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{toast.message}</span>
          </div>
        </div>
      )}
    </div>
  );
}

