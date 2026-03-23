"use client";

import type { Route } from "next";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Database,
  FileText,
  Plus,
  RefreshCw,
  Settings,
  Trash2,
  UploadCloud,
  Users,
} from "lucide-react";

import BatchUploadModal from "@/components/BatchUploadModal";
import OrganizationTree from "@/components/OrganizationTree";
import ReanalyzeAiToggle from "@/components/ReanalyzeAiToggle";
import ReanalyzeProgressDialog, {
  type ReanalyzeBatchPayload,
  type ReanalyzeLiveStatus,
} from "@/components/ReanalyzeProgressDialog";
import StructuredCleanupDialog, {
  type StructuredCleanupPreviewPayload,
} from "@/components/StructuredCleanupDialog";
import AnalysisResultsPanel from "@/components/admin/AnalysisResultsPanel";
import UserManagementPanel from "@/components/admin/UserManagementPanel";
import { cn } from "@/lib/utils";

type AdminTab = "operations" | "analysis" | "users" | "organization" | "system";

type OrganizationSelection = {
  id: string;
  name: string;
  level: string;
  parent_id: string | null;
};

type OperationNotice = {
  tone: "success" | "error" | "info";
  message: string;
};

type RematchPreviewPayload = {
  candidate_count?: number;
  updated_count?: number;
  skipped_count?: number;
  failed_count?: number;
  fast_path_hits?: number;
  pdf_text_fallback_hits?: number;
  matches?: Array<Record<string, unknown>>;
  skipped?: Array<Record<string, unknown>>;
  failed?: Array<Record<string, unknown>>;
};

type LinkRepairPreviewPayload = {
  candidate_count?: number;
  repaired_count?: number;
  linked_from_status_count?: number;
  matched_from_pdf_count?: number;
  skipped_count?: number;
  failed_count?: number;
  repairs?: Array<Record<string, unknown>>;
  skipped?: Array<Record<string, unknown>>;
  failed?: Array<Record<string, unknown>>;
};

type StructuredCleanupResult = StructuredCleanupPreviewPayload & {
  deleted_document_version_count?: number;
  updated_job_count?: number;
};

const TERMINAL_STATUSES = new Set(["done", "completed", "error", "failed"]);

const tabs: Array<{
  id: AdminTab;
  label: string;
  icon: typeof FileText;
  description: string;
}> = [
  { id: "operations", label: "运维操作", icon: Database, description: "上传、修复、重分析" },
  { id: "analysis", label: "分析结果", icon: FileText, description: "AI 与规则结果" },
  { id: "users", label: "用户管理", icon: Users, description: "账号与权限" },
  { id: "organization", label: "组织架构", icon: Users, description: "部门与单位维护" },
  { id: "system", label: "系统说明", icon: Settings, description: "当前版本说明" },
];

function isAdminTab(value: string | null): value is AdminTab {
  return tabs.some((tab) => tab.id === value);
}

function getItemTitle(item: Record<string, unknown>) {
  return String(
    item.filename ||
      item.job_id ||
      item.organization_name ||
      item.scope_name ||
      item.department_name ||
      "未命名项目",
  );
}

function getItemDescription(item: Record<string, unknown>) {
  return String(item.detail || item.reason || item.action || item.status || "没有更多说明");
}

function ResultMetrics({
  metrics,
}: {
  metrics: Array<{ label: string; value: number | string }>;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
      {metrics.map((item) => (
        <div key={item.label} className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
          <div className="text-xs text-slate-500">{item.label}</div>
          <div className="mt-2 text-lg font-semibold text-slate-900">{item.value}</div>
        </div>
      ))}
    </div>
  );
}

function ResultList({
  title,
  items,
  emptyText,
}: {
  title: string;
  items: Array<Record<string, unknown>>;
  emptyText: string;
}) {
  const rows = items.slice(0, 8);
  return (
    <div className="rounded-2xl border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-900">
        {title}
      </div>
      <div className="space-y-3 p-4">
        {rows.length === 0 ? (
          <div className="text-sm text-slate-500">{emptyText}</div>
        ) : (
          rows.map((item, index) => (
            <div
              key={`${title}-${index}`}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm"
            >
              <div className="font-medium text-slate-900">{getItemTitle(item)}</div>
              <div className="mt-1 text-slate-600">{getItemDescription(item)}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

async function postJson<T>(url: string, body: Record<string, unknown>) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = (await response.json().catch(() => ({}))) as T;
  return { response, payload };
}

async function readErrorMessage(response: Response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload?.detail || payload?.error || payload?.message || text || `HTTP ${response.status}`;
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function AdminPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const initialTab: AdminTab = isAdminTab(requestedTab) ? requestedTab : "operations";

  const [activeTab, setActiveTab] = useState<AdminTab>(initialTab);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [selectedOrg, setSelectedOrg] = useState<OrganizationSelection | null>(null);
  const [treeRefreshKey, setTreeRefreshKey] = useState(0);
  const [notice, setNotice] = useState<OperationNotice | null>(null);
  const [isRunningReanalyzeAll, setIsRunningReanalyzeAll] = useState(false);
  const [reanalyzeUseAiAssist, setReanalyzeUseAiAssist] = useState(true);
  const [hasConfiguredReanalyzeUseAiAssist, setHasConfiguredReanalyzeUseAiAssist] = useState(false);
  const [isRepairingLinks, setIsRepairingLinks] = useState(false);
  const [isRematching, setIsRematching] = useState(false);
  const [isLoadingCleanupPreview, setIsLoadingCleanupPreview] = useState(false);
  const [isExecutingCleanup, setIsExecutingCleanup] = useState(false);
  const [reanalyzeBatch, setReanalyzeBatch] = useState<ReanalyzeBatchPayload | null>(null);
  const [reanalyzeLiveStatuses, setReanalyzeLiveStatuses] = useState<
    Record<string, ReanalyzeLiveStatus>
  >({});
  const [isReanalyzeDialogOpen, setIsReanalyzeDialogOpen] = useState(false);
  const [repairPreview, setRepairPreview] = useState<LinkRepairPreviewPayload | null>(null);
  const [rematchPreview, setRematchPreview] = useState<RematchPreviewPayload | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<StructuredCleanupPreviewPayload | null>(null);
  const [isCleanupDialogOpen, setIsCleanupDialogOpen] = useState(false);

  const activeTabMeta = useMemo(() => tabs.find((tab) => tab.id === activeTab) ?? tabs[0], [activeTab]);

  useEffect(() => {
    if (isAdminTab(requestedTab) && requestedTab !== activeTab) {
      setActiveTab(requestedTab);
    }
  }, [activeTab, requestedTab]);

  useEffect(() => {
    if (!isReanalyzeDialogOpen || !reanalyzeBatch?.created?.length) {
      return;
    }
    const jobIds = reanalyzeBatch.created
      .map((item) => String(item.job_id || "").trim())
      .filter(Boolean);
    if (jobIds.length === 0) {
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      const entries = await Promise.all(
        jobIds.map(async (jobId) => {
          const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
            cache: "no-store",
          });
          const payload = (await response.json().catch(() => ({}))) as ReanalyzeLiveStatus;
          return [jobId, payload] as const;
        }),
      );

      if (cancelled) {
        return;
      }

      const nextStatuses = Object.fromEntries(entries);
      setReanalyzeLiveStatuses(nextStatuses);
      const allDone = Object.values(nextStatuses).every((item) =>
        TERMINAL_STATUSES.has(String(item.status || "").toLowerCase()),
      );
      if (!allDone) {
        timer = setTimeout(poll, 3000);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [isReanalyzeDialogOpen, reanalyzeBatch]);

  const reanalyzeSummary = useMemo(() => {
    if (!reanalyzeBatch) {
      return null;
    }
    return `已触发 ${reanalyzeBatch.created_count ?? 0} 个任务，跳过 ${reanalyzeBatch.skipped_count ?? 0} 个，失败 ${reanalyzeBatch.failed_count ?? 0} 个。`;
  }, [reanalyzeBatch]);

  const reanalyzeAllRequestBody: Record<string, unknown> = {
    latest_per_department: true,
  };
  if (hasConfiguredReanalyzeUseAiAssist) {
    reanalyzeAllRequestBody.use_local_rules = true;
    reanalyzeAllRequestBody.use_ai_assist = reanalyzeUseAiAssist;
  }

  const changeTab = (tab: AdminTab) => {
    setActiveTab(tab);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", tab);
    const href = params.toString() ? (`/admin?${params.toString()}` as Route) : ("/admin" as Route);
    router.replace(href, { scroll: false });
  };

  const refreshOrganizationTree = () => {
    setTreeRefreshKey((current) => current + 1);
  };

  const handleCreateDepartment = async () => {
    const name = window.prompt("请输入新部门名称");
    const trimmedName = String(name || "").trim();
    if (!trimmedName) {
      return;
    }

    setNotice(null);
    try {
      const response = await fetch("/api/organizations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName, level: "department" }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const payload = (await response.json()) as OrganizationSelection;
      setSelectedOrg({
        id: String(payload.id || ""),
        name: String(payload.name || trimmedName),
        level: String(payload.level || "department"),
        parent_id: payload.parent_id ?? null,
      });
      refreshOrganizationTree();
      setNotice({ tone: "success", message: `已创建部门：${trimmedName}` });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "创建部门失败。" });
    }
  };

  const handleCreateUnit = async () => {
    if (!selectedOrg || selectedOrg.level !== "department") {
      setNotice({ tone: "error", message: "请先选择一个部门，再新增下属单位。" });
      return;
    }

    const name = window.prompt(`请输入“${selectedOrg.name}”下属单位名称`);
    const trimmedName = String(name || "").trim();
    if (!trimmedName) {
      return;
    }

    setNotice(null);
    try {
      const response = await fetch(`/api/departments/${encodeURIComponent(selectedOrg.id)}/units`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmedName }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      await response.json().catch(() => ({}));
      refreshOrganizationTree();
      setNotice({ tone: "success", message: `已创建下属单位：${trimmedName}` });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "创建下属单位失败。" });
    }
  };

  const handleDeleteOrganization = async () => {
    if (!selectedOrg) {
      setNotice({ tone: "error", message: "请先选择要删除的部门或单位。" });
      return;
    }

    setNotice(null);
    try {
      const previewResponse = await fetch(
        `/api/organizations/${encodeURIComponent(selectedOrg.id)}/delete-preview`,
        { cache: "no-store" },
      );
      if (!previewResponse.ok) {
        throw new Error(await readErrorMessage(previewResponse));
      }

      const previewPayload = (await previewResponse.json()) as {
        summary?: {
          organization_count?: number;
          unit_count?: number;
          job_count?: number;
        };
      };
      const summary = previewPayload.summary || {};
      const label = selectedOrg.level === "department" ? "部门" : "单位";
      const confirmed = window.confirm(
        [
          `确定要删除${label}“${selectedOrg.name}”吗？`,
          `将删除组织 ${summary.organization_count ?? 0} 个，其中单位 ${summary.unit_count ?? 0} 个。`,
          `将影响任务关联 ${summary.job_count ?? 0} 条。`,
        ].join("\n"),
      );
      if (!confirmed) {
        return;
      }

      const response = await fetch(`/api/organizations/${encodeURIComponent(selectedOrg.id)}/delete`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const deletedName = selectedOrg.name;
      setSelectedOrg(null);
      refreshOrganizationTree();
      setNotice({ tone: "success", message: `已删除组织：${deletedName}` });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "删除组织失败。" });
    }
  };

  const runReanalyzeAll = async () => {
    setIsRunningReanalyzeAll(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<ReanalyzeBatchPayload>(
        "/api/jobs/reanalyze-all",
        reanalyzeAllRequestBody,
      );
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setReanalyzeBatch(payload);
      setReanalyzeLiveStatuses({});
      setIsReanalyzeDialogOpen(true);
      setNotice({ tone: "success", message: `已开始批量重分析，创建 ${payload.created_count ?? 0} 个任务。` });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "批量重分析失败。" });
    } finally {
      setIsRunningReanalyzeAll(false);
    }
  };

  const previewRepairLinks = async (dryRun: boolean) => {
    setIsRepairingLinks(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<LinkRepairPreviewPayload>("/api/jobs/repair-missing-links", { dry_run: dryRun });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setRepairPreview(payload);
      if (!dryRun) {
        refreshOrganizationTree();
      }
      setNotice({
        tone: dryRun ? "info" : "success",
        message: dryRun
          ? `已生成缺失关联预览，共 ${payload.candidate_count ?? 0} 条候选记录。`
          : `缺失关联修复完成，成功修复 ${payload.repaired_count ?? 0} 条记录。`,
      });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "缺失关联修复失败。" });
    } finally {
      setIsRepairingLinks(false);
    }
  };

  const previewRematchOrganizations = async (dryRun: boolean) => {
    setIsRematching(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<RematchPreviewPayload>("/api/jobs/rematch-organizations", { dry_run: dryRun });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setRematchPreview(payload);
      if (!dryRun) {
        refreshOrganizationTree();
      }
      setNotice({
        tone: dryRun ? "info" : "success",
        message: dryRun
          ? `已生成组织重匹配预览，共 ${payload.candidate_count ?? 0} 条候选记录。`
          : `组织重匹配完成，更新 ${payload.updated_count ?? 0} 条记录。`,
      });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "组织重匹配失败。" });
    } finally {
      setIsRematching(false);
    }
  };

  const previewCleanup = async () => {
    setIsLoadingCleanupPreview(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<StructuredCleanupPreviewPayload>("/api/jobs/structured-ingest-cleanup", { dry_run: true });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setCleanupPreview(payload);
      setIsCleanupDialogOpen(true);
      setNotice({ tone: "info", message: "已生成结构化历史版本清理预览，请确认后执行。" });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "结构化清理预览失败。" });
    } finally {
      setIsLoadingCleanupPreview(false);
    }
  };

  const confirmCleanup = async () => {
    setIsExecutingCleanup(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<StructuredCleanupResult>("/api/jobs/structured-ingest-cleanup", {
        dry_run: false,
        department_id: cleanupPreview?.department_id || undefined,
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setCleanupPreview(payload);
      setNotice({
        tone: "success",
        message: `结构化清理完成，删除 ${payload.deleted_document_version_count ?? 0} 个旧版本，更新 ${payload.updated_job_count ?? 0} 个任务。`,
      });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "结构化清理失败。" });
    } finally {
      setIsExecutingCleanup(false);
    }
  };

  return (
    <>
      <div className="flex h-full bg-slate-50">
        <aside className="flex w-72 shrink-0 flex-col border-r border-border bg-white">
          <div className="border-b border-border px-6 py-6">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary-600">Admin Console</div>
            <h1 className="mt-3 text-2xl font-bold text-slate-900">系统管理</h1>
            <p className="mt-2 text-sm text-slate-500">集中处理后台运维、组织维护与权限配置。</p>
          </div>
          <nav className="space-y-2 p-4">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => changeTab(tab.id)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-2xl px-4 py-3 text-left transition-all",
                    isActive ? "bg-primary-50 text-primary-900 ring-1 ring-primary-100" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                  )}
                >
                  <div className={cn("rounded-xl p-2", isActive ? "bg-white text-primary-600" : "bg-slate-100 text-slate-500")}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold">{tab.label}</div>
                    <div className="mt-1 text-xs text-slate-500">{tab.description}</div>
                  </div>
                </button>
              );
            })}
          </nav>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl p-8">
            <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-primary-600">
                  {activeTabMeta.label}
                </div>
                <h2 className="mt-2 text-3xl font-bold text-slate-900">{activeTabMeta.label}</h2>
                <p className="mt-3 text-sm text-slate-600">{activeTabMeta.description}</p>
              </div>
              {activeTab === "operations" ? (
                <button
                  type="button"
                  onClick={() => setIsUploadModalOpen(true)}
                  className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700"
                >
                  <UploadCloud className="h-4 w-4" />
                  批量上传
                </button>
              ) : null}
            </header>

            {activeTab === "operations" ? (
              <div className="space-y-6">
                {notice ? (
                  <div className={cn(
                    "rounded-2xl border px-4 py-3 text-sm",
                    notice.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-800",
                    notice.tone === "error" && "border-red-200 bg-red-50 text-red-800",
                    notice.tone === "info" && "border-blue-200 bg-blue-50 text-blue-800",
                  )}>
                    {notice.message}
                  </div>
                ) : null}

                <section className="rounded-3xl border border-border bg-white p-6 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <h3 className="text-xl font-semibold text-slate-900">全库报告接入</h3>
                      <p className="mt-2 text-sm text-slate-600">保留真实上传入口，上传后会走现有的匹配、结构化与审校流程。</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setIsUploadModalOpen(true)}
                      className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
                    >
                      <UploadCloud className="h-4 w-4" />
                      打开上传面板
                    </button>
                  </div>
                </section>

                <section className="grid gap-4 xl:grid-cols-2">
                  <div className="rounded-2xl border border-border bg-white p-6 shadow-sm">
                    <Activity className="h-5 w-5 text-slate-500" />
                    <h3 className="mt-4 text-base font-semibold text-slate-900">按组织批量重分析</h3>
                    <p className="mt-2 text-sm text-slate-500">触发各组织当前最新报告重新分析，并显示实时状态。</p>
                    <ReanalyzeAiToggle
                      checked={reanalyzeUseAiAssist}
                      onChange={(checked) => {
                        setReanalyzeUseAiAssist(checked);
                        setHasConfiguredReanalyzeUseAiAssist(true);
                      }}
                      disabled={isRunningReanalyzeAll}
                      className="mt-4 bg-white"
                      testId="admin-reanalyze-ai-toggle"
                      description="按组织批量重分析会使用这个设置；取消勾选后仅本地解析。"
                    />
                    <button
                      type="button"
                      onClick={() => void runReanalyzeAll()}
                      disabled={isRunningReanalyzeAll}
                      className="mt-5 inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 disabled:opacity-60"
                    >
                      <RefreshCw className={cn("h-4 w-4", isRunningReanalyzeAll && "animate-spin")} />
                      {isRunningReanalyzeAll ? "执行中..." : "开始重分析"}
                    </button>
                    {reanalyzeSummary ? <p className="mt-3 text-xs text-slate-500">{reanalyzeSummary}</p> : null}
                  </div>

                  <div className="rounded-2xl border border-border bg-white p-6 shadow-sm">
                    <Database className="h-5 w-5 text-slate-500" />
                    <h3 className="mt-4 text-base font-semibold text-slate-900">修复缺失关联</h3>
                    <p className="mt-2 text-sm text-slate-500">修复磁盘里存在、前台却看不到的报告与组织关系。</p>
                    <div className="mt-5 flex flex-wrap gap-3">
                      <button type="button" onClick={() => void previewRepairLinks(true)} disabled={isRepairingLinks} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-700 disabled:opacity-60">
                        预览修复
                      </button>
                      <button type="button" onClick={() => void previewRepairLinks(false)} disabled={isRepairingLinks} className="rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-60">
                        正式修复
                      </button>
                    </div>
                  </div>
                </section>

                <section className="grid gap-4 xl:grid-cols-2">
                  <div className="rounded-2xl border border-border bg-white p-6 shadow-sm">
                    <Database className="h-5 w-5 text-slate-500" />
                    <h3 className="mt-4 text-base font-semibold text-slate-900">组织重新匹配</h3>
                    <p className="mt-2 text-sm text-slate-500">在组织导入、改名后批量恢复历史报告的关联结果。</p>
                    <div className="mt-5 flex flex-wrap gap-3">
                      <button type="button" onClick={() => void previewRematchOrganizations(true)} disabled={isRematching} className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-700 disabled:opacity-60">
                        预览匹配
                      </button>
                      <button type="button" onClick={() => void previewRematchOrganizations(false)} disabled={isRematching} className="rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-60">
                        正式执行
                      </button>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-red-200 bg-red-50 p-6 shadow-sm">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2 text-red-700">
                          <AlertTriangle className="h-5 w-5" />
                          <h3 className="text-base font-semibold">清理旧结构化版本</h3>
                        </div>
                        <p className="mt-2 text-sm text-red-700/80">只清理数据库中的旧结构化版本，先预览再确认执行。</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => void previewCleanup()}
                        disabled={isLoadingCleanupPreview}
                        className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-60"
                      >
                        {isLoadingCleanupPreview ? "生成中..." : "开始预览"}
                      </button>
                    </div>
                  </div>
                </section>

                {repairPreview ? (
                  <section className="space-y-4 rounded-2xl border border-border bg-white p-6 shadow-sm">
                    <h3 className="text-base font-semibold text-slate-900">缺失关联修复结果</h3>
                    <ResultMetrics metrics={[
                      { label: "候选记录", value: repairPreview.candidate_count ?? 0 },
                      { label: "已修复", value: repairPreview.repaired_count ?? 0 },
                      { label: "按状态补链", value: repairPreview.linked_from_status_count ?? 0 },
                      { label: "按 PDF 匹配", value: repairPreview.matched_from_pdf_count ?? 0 },
                      { label: "跳过", value: repairPreview.skipped_count ?? 0 },
                      { label: "失败", value: repairPreview.failed_count ?? 0 },
                    ]} />
                    <div className="grid gap-4 xl:grid-cols-3">
                      <ResultList title="候选修复项" items={repairPreview.repairs ?? []} emptyText="当前没有候选修复项。" />
                      <ResultList title="跳过项" items={repairPreview.skipped ?? []} emptyText="当前没有跳过项。" />
                      <ResultList title="失败项" items={repairPreview.failed ?? []} emptyText="当前没有失败项。" />
                    </div>
                  </section>
                ) : null}

                {rematchPreview ? (
                  <section className="space-y-4 rounded-2xl border border-border bg-white p-6 shadow-sm">
                    <h3 className="text-base font-semibold text-slate-900">组织重匹配结果</h3>
                    <ResultMetrics metrics={[
                      { label: "候选记录", value: rematchPreview.candidate_count ?? 0 },
                      { label: "已更新", value: rematchPreview.updated_count ?? 0 },
                      { label: "文件名直匹配", value: rematchPreview.fast_path_hits ?? 0 },
                      { label: "首页文本回退", value: rematchPreview.pdf_text_fallback_hits ?? 0 },
                      { label: "跳过", value: rematchPreview.skipped_count ?? 0 },
                      { label: "失败", value: rematchPreview.failed_count ?? 0 },
                    ]} />
                    <div className="grid gap-4 xl:grid-cols-3">
                      <ResultList title="候选匹配项" items={rematchPreview.matches ?? []} emptyText="当前没有候选匹配项。" />
                      <ResultList title="跳过项" items={rematchPreview.skipped ?? []} emptyText="当前没有跳过项。" />
                      <ResultList title="失败项" items={rematchPreview.failed ?? []} emptyText="当前没有失败项。" />
                    </div>
                  </section>
                ) : null}
              </div>
            ) : null}

            {activeTab === "analysis" ? <AnalysisResultsPanel /> : null}
            {activeTab === "users" ? <UserManagementPanel embedded /> : null}

            {activeTab === "organization" ? (
              <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
                <div className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
                  <OrganizationTree
                    isAdmin
                    refreshKey={treeRefreshKey}
                    selectedOrgId={selectedOrg?.id || null}
                    onSelect={(org) => setSelectedOrg(org as OrganizationSelection | null)}
                  />
                </div>
                <div className="space-y-4">
                  <section
                    className="rounded-2xl border border-border bg-white p-6 shadow-sm"
                    data-testid="admin-org-panel"
                  >
                    <h3 className="text-lg font-semibold text-slate-900">组织管理入口</h3>
                    <p className="mt-2 text-sm text-slate-600">左侧树已经接回真实的创建、改名、删除和导入能力。</p>
                    <div className="mt-4 flex flex-wrap gap-3">
                      <button
                        type="button"
                        onClick={() => void handleCreateDepartment()}
                        data-testid="admin-org-create-department"
                        className="inline-flex items-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700"
                      >
                        <Plus className="h-4 w-4" />
                        新建部门
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleCreateUnit()}
                        disabled={!selectedOrg || selectedOrg.level !== "department"}
                        data-testid="admin-org-create-unit"
                        className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Plus className="h-4 w-4" />
                        新增下属单位
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteOrganization()}
                        disabled={!selectedOrg}
                        data-testid="admin-org-delete-current"
                        className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-700 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <Trash2 className="h-4 w-4" />
                        删除当前组织
                      </button>
                    </div>
                    {selectedOrg ? (
                      <div
                        className="mt-4 rounded-2xl bg-slate-50 p-4 text-sm text-slate-600"
                        data-testid="admin-org-selection"
                      >
                        <div className="font-semibold text-slate-900" data-testid="admin-org-selected-name">
                          {selectedOrg.name}
                        </div>
                        <div className="mt-2">当前层级：{selectedOrg.level === "department" ? "部门" : "单位"}</div>
                        <Link href={`/department/${selectedOrg.id}` as Route} className="mt-4 inline-flex rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-primary-700">
                          打开组织详情
                        </Link>
                      </div>
                    ) : (
                      <div
                        className="mt-4 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-sm text-slate-500"
                        data-testid="admin-org-selection-empty"
                      >
                        先从左侧选择一个部门或单位。
                      </div>
                    )}
                  </section>
                </div>
              </div>
            ) : null}

            {activeTab === "system" ? (
              <div className="rounded-2xl border border-border bg-white p-6 text-sm text-slate-600 shadow-sm">
                当前版本优先恢复了真实可用的后台能力：报告上传、关联修复、组织重匹配、批量重分析和组织树维护。
              </div>
            ) : null}
          </div>
        </main>
      </div>

      {isUploadModalOpen ? (
        <BatchUploadModal
          defaultDocType="dept_budget"
          onClose={() => setIsUploadModalOpen(false)}
          onComplete={() => {
            setIsUploadModalOpen(false);
            refreshOrganizationTree();
          }}
        />
      ) : null}

      <ReanalyzeProgressDialog
        isOpen={isReanalyzeDialogOpen}
        batch={reanalyzeBatch}
        liveStatuses={reanalyzeLiveStatuses}
        onClose={() => setIsReanalyzeDialogOpen(false)}
      />

      <StructuredCleanupDialog
        isOpen={isCleanupDialogOpen}
        preview={cleanupPreview}
        isExecuting={isExecutingCleanup}
        onClose={() => {
          if (!isExecutingCleanup) {
            setIsCleanupDialogOpen(false);
          }
        }}
        onConfirm={() => void confirmCleanup()}
      />
    </>
  );
}

export default function AdminPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
          <div className="rounded-2xl border border-slate-200 bg-white px-6 py-5 text-sm text-slate-600 shadow-sm">
            正在加载系统管理页面...
          </div>
        </div>
      }
    >
      <AdminPageContent />
    </Suspense>
  );
}
