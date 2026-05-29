"use client";

import {
  Database,
  FileCog,
  FileText,
  FolderTree,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Trash2,
  UploadCloud,
  UserCog,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
import type { OrganizationRecord } from "@/lib/uiAdapters";

type AdminSection =
  | "overview"
  | "organization"
  | "users"
  | "operations"
  | "analysis"
  | "rules"
  | "mappings"
  | "settings";

type OrganizationSelection = {
  id: string;
  name: string;
  level: string;
  parent_id: string | null;
};

type Notice = {
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

type ConfigCollection =
  | "rule-packages"
  | "material-mappings"
  | "system-settings"
  | "export-templates";

type ConfigItem = {
  id: string;
  name: string;
  enabled: boolean;
  description: string;
  updated_at: string;
  updated_by: string;
  created_at: string;
  created_by: string;
  data: Record<string, unknown>;
};

type SystemManagementPanelProps = {
  organizations: OrganizationRecord[];
  onRefresh?: () => Promise<void> | void;
};

const TERMINAL_STATUSES = new Set(["done", "completed", "error", "failed"]);

const SECTIONS: Array<{
  id: AdminSection;
  label: string;
  description: string;
  icon: typeof Settings;
}> = [
  { id: "overview", label: "基础档案总览", description: "系统管理入口与配置概览", icon: Settings },
  { id: "organization", label: "组织与单位", description: "组织树、部门、单位、导入与删除", icon: FolderTree },
  { id: "users", label: "用户与权限", description: "账号、角色与登录权限", icon: UserCog },
  { id: "operations", label: "运维操作", description: "上传、重分析、修复与清理", icon: Database },
  { id: "analysis", label: "分析结果", description: "AI 与规则分析结果入库查看", icon: FileText },
  { id: "rules", label: "规则包与口径", description: "规则包版本、启用状态和说明", icon: KeyRound },
  { id: "mappings", label: "材料类型与字段映射", description: "材料类型、字段别名和表格映射", icon: SlidersHorizontal },
  { id: "settings", label: "系统参数与导出模板", description: "参数记录和导出模板配置", icon: FileCog },
];

function cn(...items: Array<string | false | null | undefined>): string {
  return items.filter(Boolean).join(" ");
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
    const payload = JSON.parse(text) as Record<string, unknown>;
    return String(payload.detail || payload.error || payload.message || text || `HTTP ${response.status}`);
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function formatDate(value?: string | null) {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function displayOrgName(value?: string | null, level?: string | null): string {
  const name = String(value || "").trim() || "未命名组织";
  if (level !== "unit") {
    return name;
  }
  const parts = name.split(/\s+/);
  return parts.length > 1 ? parts[parts.length - 1] : name;
}

function orgLevelLabel(level?: string | null): string {
  return level === "department" ? "部门" : level === "unit" ? "单位" : "组织";
}

function Card({
  title,
  children,
  className,
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-md border border-slate-200 bg-white shadow-sm", className)}>
      {title ? <div className="border-b border-slate-100 px-5 py-4 text-lg font-black text-slate-900">{title}</div> : null}
      {children}
    </section>
  );
}

function NoticeBanner({ notice }: { notice: Notice | null }) {
  if (!notice) {
    return null;
  }
  return (
    <div
      className={cn(
        "rounded-md border px-4 py-3 text-sm font-semibold",
        notice.tone === "success" && "border-emerald-200 bg-emerald-50 text-emerald-800",
        notice.tone === "error" && "border-red-200 bg-red-50 text-red-800",
        notice.tone === "info" && "border-blue-200 bg-blue-50 text-blue-800",
      )}
    >
      {notice.message}
    </div>
  );
}

function ResultMetrics({
  metrics,
}: {
  metrics: Array<{ label: string; value: number | string }>;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
      {metrics.map((item) => (
        <div key={item.label} className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
          <div className="text-xs text-slate-500">{item.label}</div>
          <div className="mt-2 text-lg font-black text-slate-900">{item.value}</div>
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
    <div className="rounded-md border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3 text-sm font-black text-slate-900">
        {title}
      </div>
      <div className="space-y-3 p-4">
        {rows.length === 0 ? (
          <div className="text-sm text-slate-500">{emptyText}</div>
        ) : (
          rows.map((item, index) => (
            <div key={`${title}-${index}`} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm">
              <div className="font-bold text-slate-900">
                {String(item.filename || item.job_id || item.organization_name || item.scope_name || item.department_name || "未命名项目")}
              </div>
              <div className="mt-1 text-slate-600">
                {String(item.detail || item.reason || item.action || item.status || "没有更多说明")}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function OverviewSection({
  organizations,
  setSection,
}: {
  organizations: OrganizationRecord[];
  setSection: (section: AdminSection) => void;
}) {
  const totalIssues = organizations.reduce((sum, org) => sum + Number(org.issue_count ?? 0), 0);
  const totalJobs = organizations.reduce((sum, org) => sum + Number(org.job_count ?? 0), 0);
  const cards: Array<{
    id: AdminSection;
    icon: typeof Settings;
    title: string;
    desc: string;
    metric: string;
  }> = [
    { id: "organization", icon: FolderTree, title: "组织与单位", desc: "维护部门、单位、导入模板和删除影响范围。", metric: `${organizations.length} 个组织` },
    { id: "users", icon: UserCog, title: "用户与权限", desc: "管理账号、管理员角色、启停和密码重置。", metric: "账号权限" },
    { id: "operations", icon: Database, title: "运维操作", desc: "上传、重分析、缺失关联修复和结构化清理。", metric: `${totalJobs} 个任务` },
    { id: "analysis", icon: FileText, title: "分析结果", desc: "查看 AI、规则与结构化入库结果。", metric: `${totalIssues} 个问题` },
    { id: "rules", icon: KeyRound, title: "规则包与口径", desc: "维护规则包说明、版本和启用状态。", metric: "配置留档" },
    { id: "mappings", icon: SlidersHorizontal, title: "材料类型与字段映射", desc: "维护材料类型、字段别名和表格映射。", metric: "配置留档" },
    { id: "settings", icon: FileCog, title: "系统参数与导出模板", desc: "维护参数和导出模板描述。", metric: "配置留档" },
  ];

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-white p-5">
          <div className="text-sm text-slate-500">组织数量</div>
          <div className="mt-2 text-3xl font-black text-slate-950">{organizations.length}</div>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-5">
          <div className="text-sm text-slate-500">关联任务</div>
          <div className="mt-2 text-3xl font-black text-slate-950">{totalJobs}</div>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-5">
          <div className="text-sm text-slate-500">待处理问题</div>
          <div className="mt-2 text-3xl font-black text-red-600">{totalIssues}</div>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-5">
          <div className="text-sm text-slate-500">配置域</div>
          <div className="mt-2 text-3xl font-black text-blue-700">4</div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <button
              key={card.id}
              type="button"
              onClick={() => setSection(card.id)}
              className="min-h-[148px] rounded-md border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-blue-200 hover:bg-blue-50/40"
            >
              <div className="flex items-start gap-4">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-blue-600 text-white">
                  <Icon className="h-5 w-5" />
                </span>
                <span>
                  <span className="block text-lg font-black text-slate-950">{card.title}</span>
                  <span className="mt-2 block text-sm leading-6 text-slate-600">{card.desc}</span>
                  <span className="mt-3 inline-flex rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">
                    {card.metric}
                  </span>
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function OrganizationSection() {
  const [selectedOrg, setSelectedOrg] = useState<OrganizationSelection | null>(null);
  const [treeRefreshKey, setTreeRefreshKey] = useState(0);
  const [notice, setNotice] = useState<Notice | null>(null);

  const refreshOrganizationTree = async () => {
    setTreeRefreshKey((current) => current + 1);
  };

  const handleCreateDepartment = async () => {
    const parentOrg = selectedOrg && selectedOrg.level !== "unit" ? selectedOrg : null;
    const name = window.prompt(
      parentOrg ? `请输入“${parentOrg.name}”下级部门名称` : "请输入新部门名称",
    );
    const trimmedName = String(name || "").trim();
    if (!trimmedName) {
      return;
    }

    setNotice(null);
    try {
      const requestBody: Record<string, string> = { name: trimmedName, level: "department" };
      if (parentOrg) {
        requestBody.parent_id = parentOrg.id;
      }

      const response = await fetch("/api/organizations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const payload = (await response.json()) as OrganizationSelection;
      setSelectedOrg({
        id: String(payload.id || ""),
        name: String(payload.name || trimmedName),
        level: String(payload.level || "department"),
        parent_id: payload.parent_id ?? parentOrg?.id ?? null,
      });
      await refreshOrganizationTree();
      setNotice({
        tone: "success",
        message: parentOrg ? `已创建“${parentOrg.name}”下级部门：${trimmedName}` : `已创建部门：${trimmedName}`,
      });
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

      const payload = (await response.json().catch(() => ({}))) as Partial<OrganizationSelection>;
      setSelectedOrg({
        id: String(payload.id || ""),
        name: String(payload.name || trimmedName),
        level: String(payload.level || "unit"),
        parent_id: payload.parent_id ?? selectedOrg.id,
      });
      await refreshOrganizationTree();
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
      await refreshOrganizationTree();
      setNotice({ tone: "success", message: `已删除组织：${deletedName}` });
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "删除组织失败。" });
    }
  };

  const createDepartmentParent = selectedOrg && selectedOrg.level !== "unit" ? selectedOrg : null;

  return (
    <div className="space-y-4">
      <NoticeBanner notice={notice} />
      <div className="grid items-start gap-5 xl:grid-cols-[460px_minmax(0,1fr)]">
        <div className="h-[calc(100vh-260px)] min-h-[640px] overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
          <OrganizationTree
            isAdmin
            refreshKey={treeRefreshKey}
            selectedOrgId={selectedOrg?.id || null}
            onSelect={(org) => setSelectedOrg(org as OrganizationSelection | null)}
          />
        </div>
        <div className="space-y-4">
          <Card title="组织管理入口">
            <div className="p-5" data-testid="admin-org-panel">
              <p className="text-sm leading-6 text-slate-600">左侧组织树已经接回真实的创建、改名、删除和导入能力。</p>
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => void handleCreateDepartment()}
                  data-testid="admin-org-create-department"
                  title={createDepartmentParent ? `在“${createDepartmentParent.name}”下新建部门` : "新建顶层部门"}
                  className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-blue-700"
                >
                  <Plus className="h-4 w-4" />
                  {createDepartmentParent ? "新建下级部门" : "新建部门"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleCreateUnit()}
                  disabled={!selectedOrg || selectedOrg.level !== "department"}
                  data-testid="admin-org-create-unit"
                  className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Plus className="h-4 w-4" />
                  新增下属单位
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteOrganization()}
                  disabled={!selectedOrg}
                  data-testid="admin-org-delete-current"
                  className="inline-flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-bold text-red-700 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Trash2 className="h-4 w-4" />
                  删除当前组织
                </button>
              </div>
              {selectedOrg ? (
                <div className="mt-4 rounded-md bg-slate-50 p-4 text-sm text-slate-600" data-testid="admin-org-selection">
                  <div className="font-black text-slate-900" data-testid="admin-org-selected-name">
                    {selectedOrg.name}
                  </div>
                  <div className="mt-2">当前层级：{orgLevelLabel(selectedOrg.level)}</div>
                </div>
              ) : (
                <div
                  className="mt-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-sm text-slate-500"
                  data-testid="admin-org-selection-empty"
                >
                  先从左侧选择一个部门或单位。
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function OperationsSection({ onRefresh }: { onRefresh?: () => Promise<void> | void }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [isRunningReanalyzeAll, setIsRunningReanalyzeAll] = useState(false);
  const [reanalyzeUseAiAssist, setReanalyzeUseAiAssist] = useState(true);
  const [hasConfiguredReanalyzeUseAiAssist, setHasConfiguredReanalyzeUseAiAssist] = useState(false);
  const [isRepairingLinks, setIsRepairingLinks] = useState(false);
  const [isRematching, setIsRematching] = useState(false);
  const [isLoadingCleanupPreview, setIsLoadingCleanupPreview] = useState(false);
  const [isExecutingCleanup, setIsExecutingCleanup] = useState(false);
  const [reanalyzeBatch, setReanalyzeBatch] = useState<ReanalyzeBatchPayload | null>(null);
  const [reanalyzeLiveStatuses, setReanalyzeLiveStatuses] = useState<Record<string, ReanalyzeLiveStatus>>({});
  const [isReanalyzeDialogOpen, setIsReanalyzeDialogOpen] = useState(false);
  const [repairPreview, setRepairPreview] = useState<LinkRepairPreviewPayload | null>(null);
  const [rematchPreview, setRematchPreview] = useState<RematchPreviewPayload | null>(null);
  const [cleanupPreview, setCleanupPreview] = useState<StructuredCleanupPreviewPayload | null>(null);
  const [isCleanupDialogOpen, setIsCleanupDialogOpen] = useState(false);

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

  const reanalyzeAllRequestBody: Record<string, unknown> = {
    latest_per_department: true,
  };
  if (hasConfiguredReanalyzeUseAiAssist) {
    reanalyzeAllRequestBody.use_local_rules = true;
    reanalyzeAllRequestBody.use_ai_assist = reanalyzeUseAiAssist;
  }

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
      await onRefresh?.();
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
      setNotice({
        tone: dryRun ? "info" : "success",
        message: dryRun
          ? `已生成缺失关联预览，共 ${payload.candidate_count ?? 0} 条候选记录。`
          : `缺失关联修复完成，成功修复 ${payload.repaired_count ?? 0} 条记录。`,
      });
      if (!dryRun) {
        await onRefresh?.();
      }
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
      setNotice({
        tone: dryRun ? "info" : "success",
        message: dryRun
          ? `已生成组织重匹配预览，共 ${payload.candidate_count ?? 0} 条候选记录。`
          : `组织重匹配完成，更新 ${payload.updated_count ?? 0} 条记录。`,
      });
      if (!dryRun) {
        await onRefresh?.();
      }
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
      await onRefresh?.();
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "结构化清理失败。" });
    } finally {
      setIsExecutingCleanup(false);
    }
  };

  return (
    <div className="space-y-5">
      <NoticeBanner notice={notice} />
      <Card title="全库报告接入">
        <div className="flex flex-wrap items-start justify-between gap-4 p-5">
          <p className="max-w-2xl text-sm leading-6 text-slate-600">保留真实上传入口，上传后会走现有的匹配、结构化与审校流程。</p>
          <button
            type="button"
            onClick={() => setIsUploadModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-blue-700"
          >
            <UploadCloud className="h-4 w-4" />
            打开上传面板
          </button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="按组织批量重分析">
          <div className="p-5">
            <p className="text-sm leading-6 text-slate-600">触发各组织当前最新报告重新分析，并显示实时状态。</p>
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
              className="mt-5 inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 disabled:opacity-60"
            >
              <RefreshCw className={cn("h-4 w-4", isRunningReanalyzeAll && "animate-spin")} />
              {isRunningReanalyzeAll ? "执行中..." : "开始重分析"}
            </button>
          </div>
        </Card>

        <Card title="修复缺失关联">
          <div className="p-5">
            <p className="text-sm leading-6 text-slate-600">修复磁盘里存在、前台却看不到的报告与组织关系。</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <button type="button" onClick={() => void previewRepairLinks(true)} disabled={isRepairingLinks} className="rounded-md border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-700 disabled:opacity-60">
                预览修复
              </button>
              <button type="button" onClick={() => void previewRepairLinks(false)} disabled={isRepairingLinks} className="rounded-md bg-blue-600 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60">
                正式修复
              </button>
            </div>
          </div>
        </Card>

        <Card title="组织重新匹配">
          <div className="p-5">
            <p className="text-sm leading-6 text-slate-600">在组织导入、改名后批量恢复历史报告的关联结果。</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <button type="button" onClick={() => void previewRematchOrganizations(true)} disabled={isRematching} className="rounded-md border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-700 disabled:opacity-60">
                预览匹配
              </button>
              <button type="button" onClick={() => void previewRematchOrganizations(false)} disabled={isRematching} className="rounded-md bg-blue-600 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60">
                正式执行
              </button>
            </div>
          </div>
        </Card>

        <Card title="清理旧结构化版本">
          <div className="border border-red-100 bg-red-50 p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <p className="max-w-xl text-sm leading-6 text-red-700">只清理数据库中的旧结构化版本，先预览再确认执行。</p>
              <button
                type="button"
                onClick={() => void previewCleanup()}
                disabled={isLoadingCleanupPreview}
                className="rounded-md bg-red-600 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60"
              >
                {isLoadingCleanupPreview ? "生成中..." : "开始预览"}
              </button>
            </div>
          </div>
        </Card>
      </div>

      {repairPreview ? (
        <Card title="缺失关联修复结果">
          <div className="space-y-4 p-5">
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
          </div>
        </Card>
      ) : null}

      {rematchPreview ? (
        <Card title="组织重匹配结果">
          <div className="space-y-4 p-5">
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
          </div>
        </Card>
      ) : null}

      {isUploadModalOpen ? (
        <BatchUploadModal
          defaultDocType="dept_budget"
          onClose={() => setIsUploadModalOpen(false)}
          onComplete={() => {
            setIsUploadModalOpen(false);
            void onRefresh?.();
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
    </div>
  );
}

function ConfigPanel({
  collection,
  title,
  description,
  placeholder,
}: {
  collection: ConfigCollection;
  title: string;
  description: string;
  placeholder: Record<string, unknown>;
}) {
  const [items, setItems] = useState<ConfigItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [descriptionValue, setDescriptionValue] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [dataText, setDataText] = useState(JSON.stringify(placeholder, null, 2));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  const loadItems = async () => {
    setLoading(true);
    try {
      const response = await fetch(`/api/admin/config/${collection}`, { cache: "no-store" });
      const payload = (await response.json().catch(() => ({}))) as { items?: ConfigItem[]; detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || "配置加载失败");
      }
      const nextItems = Array.isArray(payload.items) ? payload.items : [];
      setItems(nextItems);
      if (selectedId && !nextItems.some((item) => item.id === selectedId)) {
        setSelectedId("");
      }
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "配置加载失败" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadItems();
    // collection changes only when a different config panel mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collection]);

  const resetForm = () => {
    setSelectedId("");
    setName("");
    setDescriptionValue("");
    setEnabled(true);
    setDataText(JSON.stringify(placeholder, null, 2));
  };

  const editItem = (item: ConfigItem) => {
    setSelectedId(item.id);
    setName(item.name);
    setDescriptionValue(item.description || "");
    setEnabled(item.enabled);
    setDataText(JSON.stringify(item.data || {}, null, 2));
  };

  const saveItem = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNotice({ tone: "error", message: "请输入配置名称。" });
      return;
    }

    let data: Record<string, unknown>;
    try {
      const parsed = JSON.parse(dataText || "{}") as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("JSON 必须是对象");
      }
      data = parsed as Record<string, unknown>;
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "JSON 格式不正确。" });
      return;
    }

    setSaving(true);
    setNotice(null);
    try {
      const response = await fetch(
        selectedId ? `/api/admin/config/${collection}/${encodeURIComponent(selectedId)}` : `/api/admin/config/${collection}`,
        {
          method: selectedId ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: trimmedName,
            description: descriptionValue,
            enabled,
            data,
          }),
        },
      );
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || "保存失败");
      }
      setNotice({ tone: "success", message: selectedId ? "配置已更新。" : "配置已创建。" });
      resetForm();
      await loadItems();
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "保存失败。" });
    } finally {
      setSaving(false);
    }
  };

  const deleteItem = async (item: ConfigItem) => {
    if (!window.confirm(`确定删除配置“${item.name}”吗？`)) {
      return;
    }
    setSaving(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/admin/config/${collection}/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || "删除失败");
      }
      setNotice({ tone: "success", message: "配置已删除。" });
      if (selectedId === item.id) {
        resetForm();
      }
      await loadItems();
    } catch (error) {
      setNotice({ tone: "error", message: error instanceof Error ? error.message : "删除失败。" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={title}>
      <div className="space-y-4 p-5">
        <p className="text-sm leading-6 text-slate-600">{description}</p>
        <NoticeBanner notice={notice} />
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="rounded-md border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <span className="text-sm font-black text-slate-900">配置列表</span>
              <button type="button" onClick={resetForm} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-700">
                新建
              </button>
            </div>
            <div className="max-h-[520px] overflow-y-auto p-3">
              {loading ? (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载配置...
                </div>
              ) : items.length === 0 ? (
                <div className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                  暂无配置记录。
                </div>
              ) : (
                <div className="space-y-3">
                  {items.map((item) => (
                    <article key={item.id} className={cn("rounded-md border p-4", selectedId === item.id ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white")}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="font-black text-slate-900">{item.name}</div>
                          <div className="mt-1 text-sm text-slate-600">{item.description || "没有说明"}</div>
                          <div className="mt-2 text-xs text-slate-500">更新：{formatDate(item.updated_at)} / {item.updated_by}</div>
                        </div>
                        <span className={cn("rounded-full px-2.5 py-1 text-xs font-bold", item.enabled ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600")}>
                          {item.enabled ? "启用" : "停用"}
                        </span>
                      </div>
                      <pre className="mt-3 max-h-36 overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
                        {JSON.stringify(item.data, null, 2)}
                      </pre>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button type="button" onClick={() => editItem(item)} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-700">
                          编辑
                        </button>
                        <button type="button" onClick={() => void deleteItem(item)} disabled={saving} className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-bold text-red-700 disabled:opacity-60">
                          删除
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="rounded-md border border-slate-200 p-4">
            <h3 className="text-base font-black text-slate-900">{selected ? "编辑配置" : "新建配置"}</h3>
            <div className="mt-4 space-y-3">
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="配置名称"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500"
              />
              <textarea
                value={descriptionValue}
                onChange={(event) => setDescriptionValue(event.target.value)}
                placeholder="配置说明"
                rows={3}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500"
              />
              <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
                启用
              </label>
              <textarea
                value={dataText}
                onChange={(event) => setDataText(event.target.value)}
                rows={14}
                spellCheck={false}
                className="w-full rounded-md border border-slate-300 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-100 outline-none focus:border-blue-500"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void saveItem()}
                  disabled={saving}
                  className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2.5 text-sm font-black text-white disabled:opacity-60"
                >
                  {saving ? "保存中..." : "保存配置"}
                </button>
                {selected ? (
                  <button type="button" onClick={resetForm} className="rounded-md border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-700">
                    取消
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

function SettingsTemplatesSection() {
  return (
    <div className="space-y-5">
      <ConfigPanel
        collection="system-settings"
        title="系统参数"
        description="第一版仅保存后台配置记录，不改变运行时环境变量或真实审校逻辑。"
        placeholder={{ key: "AI_ASSIST_ENABLED", value: "true", scope: "runtime-note" }}
      />
      <ConfigPanel
        collection="export-templates"
        title="导出模板"
        description="维护导出模板说明、适用材料和模板元数据；第一版不接管真实导出生成链路。"
        placeholder={{ template_code: "default_rectification_package", formats: ["pdf", "zip"], fields: [] }}
      />
    </div>
  );
}

export default function SystemManagementPanel({
  organizations,
  onRefresh,
}: SystemManagementPanelProps) {
  const [activeSection, setActiveSection] = useState<AdminSection>("overview");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const section = params.get("section") as AdminSection | null;
    if (section && SECTIONS.some((item) => item.id === section)) {
      setActiveSection(section);
    }
  }, []);

  const activeMeta = useMemo(
    () => SECTIONS.find((section) => section.id === activeSection) ?? SECTIONS[0],
    [activeSection],
  );

  return (
    <div className="grid min-h-[760px] grid-cols-[280px_minmax(0,1fr)] gap-5">
      <aside className="self-start rounded-md border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 p-5">
          <div className="text-xs font-black uppercase tracking-[0.18em] text-blue-600">Admin Console</div>
          <h2 className="mt-3 text-2xl font-black text-slate-950">系统管理</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">后台运维、组织维护、权限配置与配置留档。</p>
        </div>
        <nav className="space-y-1 p-3">
          {SECTIONS.map((section) => {
            const Icon = section.icon;
            const isActive = activeSection === section.id;
            return (
              <button
                key={section.id}
                type="button"
                onClick={() => setActiveSection(section.id)}
                className={cn(
                  "flex w-full items-start gap-3 rounded-md px-3 py-3 text-left transition",
                  isActive ? "bg-blue-50 text-blue-900 ring-1 ring-blue-100" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )}
              >
                <span className={cn("rounded-md p-2", isActive ? "bg-white text-blue-600" : "bg-slate-100 text-slate-500")}>
                  <Icon className="h-4 w-4" />
                </span>
                <span>
                  <span className="block text-sm font-black">{section.label}</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-500">{section.description}</span>
                </span>
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="min-w-0 space-y-5">
        <header className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs font-black uppercase tracking-[0.18em] text-blue-600">{activeMeta.label}</div>
              <h1 className="mt-2 text-3xl font-black text-slate-950">{activeMeta.label}</h1>
              <p className="mt-3 text-sm leading-6 text-slate-600">{activeMeta.description}</p>
            </div>
            <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">
              新系统管理内置页面
            </div>
          </div>
        </header>

        {activeSection === "overview" ? <OverviewSection organizations={organizations} setSection={setActiveSection} /> : null}
        {activeSection === "organization" ? <OrganizationSection /> : null}
        {activeSection === "users" ? <UserManagementPanel embedded /> : null}
        {activeSection === "operations" ? <OperationsSection onRefresh={onRefresh} /> : null}
        {activeSection === "analysis" ? <AnalysisResultsPanel /> : null}
        {activeSection === "rules" ? (
          <ConfigPanel
            collection="rule-packages"
            title="规则包与口径配置"
            description="维护规则包版本、口径说明、启用状态和结构化配置记录。第一版只做后台留档，不影响当前 YAML 规则加载。"
            placeholder={{ version: "v3_3_portable", rules: [], tolerance: { amount_wan: 0.5, percent: 0.1 } }}
          />
        ) : null}
        {activeSection === "mappings" ? (
          <ConfigPanel
            collection="material-mappings"
            title="材料类型与字段映射"
            description="维护材料类型、字段别名和表格字段映射关系。第一版保存配置记录，后续再接解析链路。"
            placeholder={{ material_type: "dept_budget", aliases: ["部门预算"], field_mappings: [] }}
          />
        ) : null}
        {activeSection === "settings" ? <SettingsTemplatesSection /> : null}
      </main>
    </div>
  );
}
