"use client";

import {
  CalendarDays,
  Database,
  FileCog,
  FileText,
  FolderTree,
  KeyRound,
  LayoutTemplate,
  Loader2,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Trash2,
  UploadCloud,
  UserCog,
} from "lucide-react";
import { useEffect, useState } from "react";

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
import AIModelSettingsPanel from "@/components/admin/AIModelSettingsPanel";
import { useAIAnalysisOptions } from "@/components/AIAnalysisOptions";
import AnnualTemplatePanel from "@/components/settings/AnnualTemplatePanel";
import DisclosureCalendarPanel from "@/components/settings/DisclosureCalendarPanel";
import { Badge, Button, Card } from "@/components/ui";
import type { OrganizationRecord } from "@/lib/uiAdapters";
import { cn } from "@/lib/utils";

type AdminSection =
  | "overview"
  | "organization"
  | "users"
  | "operations"
  | "analysis"
  | "rules"
  | "mappings"
  | "templates"
  | "calendar"
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
  dry_run?: boolean;
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
  dry_run?: boolean;
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

/** 组织列表的读取状态：读取中或失败时概览不得把空数组当成"0 个组织"展示。 */
export type OrganizationsLoadStatus = "loading" | "ready" | "failed";

type SystemManagementPanelProps = {
  organizations: OrganizationRecord[];
  organizationsStatus?: OrganizationsLoadStatus;
  onRefresh?: () => Promise<void> | void;
};

const TERMINAL_STATUSES = new Set(["done", "completed", "review_required", "degraded", "error", "failed", "cancelled"]);

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
  { id: "templates", label: "模板管理", description: "年度模板版本与适用关系", icon: LayoutTemplate },
  { id: "calendar", label: "年度公开时限", description: "预算、决算公开截止日", icon: CalendarDays },
  { id: "settings", label: "大模型与系统参数", description: "大模型配置、系统参数和导出模板", icon: FileCog },
];

const FIELD_CLASS = "w-full rounded-md border border-border bg-white text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 px-3 py-2";

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
    const message = String(payload.detail || payload.error || payload.message || text || `HTTP ${response.status}`);
    if (/too many requests/i.test(message)) {
      return "当前操作请求过于频繁，后端暂时限流。请稍等一分钟后重试。";
    }
    return message;
  } catch {
    if (/too many requests/i.test(text)) {
      return "当前操作请求过于频繁，后端暂时限流。请稍等一分钟后重试。";
    }
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

function NoticeBanner({ notice, testId }: { notice: Notice | null; testId?: string }) {
  if (!notice) {
    return null;
  }
  return (
    <div
      data-testid={testId}
      className={cn(
        "rounded-md border px-4 py-3 text-sm font-medium",
        notice.tone === "success" && "border-success-200 bg-success-50 text-success-800",
        notice.tone === "error" && "border-danger-200 bg-danger-50 text-danger-800",
        notice.tone === "info" && "border-info-200 bg-info-50 text-info-800",
      )}
    >
      {notice.message}
    </div>
  );
}

function ResultMetrics({
  metrics,
  testId,
}: {
  metrics: Array<{ label: string; value: number | string }>;
  testId?: string;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6" data-testid={testId}>
      {metrics.map((item) => (
        <div key={item.label} className="rounded-md border border-border bg-slate-50 px-4 py-3">
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
  const [showAll, setShowAll] = useState(false);
  const rows = showAll ? items : items.slice(0, 8);
  return (
    <div className="rounded-md border border-border bg-white">
      <div className="border-b border-border px-4 py-3 text-sm font-semibold text-slate-900">
        {title}
      </div>
      <div className="space-y-3 p-4">
        {rows.length === 0 ? (
          <div className="text-sm text-slate-500">{emptyText}</div>
        ) : (
          rows.map((item, index) => (
            <div key={`${title}-${index}`} className="rounded-md border border-border bg-slate-50 px-3 py-3 text-sm">
              <div className="font-medium text-slate-900">
                {String(item.filename || item.job_id || item.organization_name || item.scope_name || item.department_name || "未命名项目")}
              </div>
              <div className="mt-1 text-slate-600">
                {String(item.detail || item.reason || item.action || item.status || "没有更多说明")}
              </div>
              {item.suggested && typeof item.suggested === "object" ? <div className="mt-1 text-slate-700">
                建议归属：{Array.from(new Set([
                  (item.suggested as Record<string, unknown>).department_name,
                  (item.suggested as Record<string, unknown>).organization_name,
                ].filter(Boolean).map(String))).join(" / ")}
              </div> : null}
            </div>
          ))
        )}
        {items.length > 8 ? <button type="button" className="text-primary-700" onClick={() => setShowAll(!showAll)}>
          {showAll ? "收起" : `查看全部 ${items.length} 项`}
        </button> : null}
      </div>
    </div>
  );
}

function OverviewSection({
  organizations,
  organizationsStatus,
  setSection,
}: {
  organizations: OrganizationRecord[];
  organizationsStatus: OrganizationsLoadStatus;
  setSection: (section: AdminSection) => void;
}) {
  // 概览不再展示"关联任务 / 待处理问题 / 配置域"三个数：组织列表按 stats=none 读取，
  // 不含任务数与问题数，汇总结果恒为 0；"配置域"是写死的常量。没有真实来源的数不展示。
  const organizationMetric =
    organizationsStatus === "ready"
      ? `${organizations.length} 个组织`
      : organizationsStatus === "failed"
        ? "组织读取失败"
        : "组织读取中…";
  const cards: Array<{
    id: AdminSection;
    icon: typeof Settings;
    title: string;
    desc: string;
    metric: string;
  }> = [
    { id: "organization", icon: FolderTree, title: "组织与单位", desc: "维护部门、单位、导入模板和删除影响范围。", metric: organizationMetric },
    { id: "users", icon: UserCog, title: "用户与权限", desc: "管理账号、管理员角色、启停和密码重置。", metric: "账号权限" },
    { id: "operations", icon: Database, title: "运维操作", desc: "上传、重分析、缺失关联修复和结构化清理。", metric: "上传与修复" },
    { id: "analysis", icon: FileText, title: "分析结果", desc: "查看 AI、规则与结构化入库结果。", metric: "结果查询" },
    { id: "rules", icon: KeyRound, title: "规则包与口径", desc: "维护规则包说明、版本和启用状态。", metric: "配置留档" },
    { id: "mappings", icon: SlidersHorizontal, title: "材料类型与字段映射", desc: "维护材料类型、字段别名和表格映射。", metric: "配置留档" },
    { id: "templates", icon: LayoutTemplate, title: "模板管理", desc: "维护年度模板版本和适用关系。", metric: "年度模板" },
    { id: "calendar", icon: CalendarDays, title: "年度公开时限", desc: "记录预算、决算公开截止日。", metric: "公开截止日" },
    { id: "settings", icon: FileCog, title: "大模型与系统参数", desc: "配置模型和导出模板。", metric: "模型配置" },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <button
            key={card.id}
            type="button"
            data-testid={`admin-overview-card-${card.id}`}
            onClick={() => setSection(card.id)}
            className="flex items-start gap-3 rounded-card border border-border bg-white p-4 text-left shadow-soft transition-colors hover:border-primary-200 hover:bg-primary-50/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary-50 text-primary-700">
              <Icon className="h-4 w-4" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-slate-900">{card.title}</span>
              <span className="mt-1 block text-xs leading-5 text-slate-500">{card.desc}</span>
              <Badge className="mt-2">{card.metric}</Badge>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function OrganizationSection({
  organizations,
  onRefresh,
}: {
  organizations: OrganizationRecord[];
  onRefresh?: () => Promise<void> | void;
}) {
  const [selectedOrg, setSelectedOrg] = useState<OrganizationSelection | null>(null);
  const [treeRefreshKey, setTreeRefreshKey] = useState(0);
  const [notice, setNotice] = useState<Notice | null>(null);

  const refreshOrganizationTree = async () => {
    setTreeRefreshKey((current) => current + 1);
    await onRefresh?.();
  };

  const handleCreateDepartment = async () => {
    if (selectedOrg && !["city", "district"].includes(selectedOrg.level)) {
      setNotice({ tone: "error", message: "部门下请新增单位；新建部门请先选择市、区或全部组织。" });
      return;
    }
    const parentOrg = selectedOrg;
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

  const canCreateDepartment = !selectedOrg || ["city", "district"].includes(selectedOrg.level);
  const createDepartmentParent = canCreateDepartment ? selectedOrg : null;

  return (
    <div className="space-y-4">
      <NoticeBanner notice={notice} testId="admin-organization-notice" />
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="h-[calc(100vh-260px)] min-h-[640px] overflow-hidden rounded-card border border-border bg-white shadow-soft">
          <OrganizationTree
            isAdmin
            fallbackOrganizations={organizations}
            refreshKey={treeRefreshKey}
            selectedOrgId={selectedOrg?.id || null}
            onSelect={(org) => setSelectedOrg(org as OrganizationSelection | null)}
            onChanged={refreshOrganizationTree}
          />
        </div>
        <Card title="组织管理">
          <div data-testid="admin-org-panel">
            <p className="text-sm leading-6 text-slate-600">在左侧组织树中可以新建、改名、删除和导入组织；选中部门或单位后，也可以在这里操作。</p>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button
                onClick={() => void handleCreateDepartment()}
                data-testid="admin-org-create-department"
                disabled={!canCreateDepartment}
                title={!canCreateDepartment ? "部门下请新增单位；请选择市、区或全部组织后新建部门" : createDepartmentParent ? `在“${createDepartmentParent.name}”下新建部门` : "新建顶层部门"}
              >
                <Plus className="h-4 w-4" />
                {createDepartmentParent ? "新建下级部门" : "新建部门"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => void handleCreateUnit()}
                disabled={!selectedOrg || selectedOrg.level !== "department"}
                data-testid="admin-org-create-unit"
              >
                <Plus className="h-4 w-4" />
                新增下属单位
              </Button>
              <Button
                variant="secondary"
                onClick={() => void handleDeleteOrganization()}
                disabled={!selectedOrg}
                data-testid="admin-org-delete-current"
                className="border-danger-200 text-danger-700 hover:bg-danger-50"
              >
                <Trash2 className="h-4 w-4" />
                删除当前组织
              </Button>
            </div>
            {selectedOrg ? (
              <div className="mt-4 rounded-md bg-surface-100 p-4 text-sm text-slate-600" data-testid="admin-org-selection">
                <div className="font-semibold text-slate-900" data-testid="admin-org-selected-name">
                  {selectedOrg.name}
                </div>
                <div className="mt-2">当前层级：{orgLevelLabel(selectedOrg.level)}</div>
              </div>
            ) : (
              <div
                className="mt-4 rounded-md border border-dashed border-slate-300 bg-surface-100 px-4 py-5 text-sm text-slate-500"
                data-testid="admin-org-selection-empty"
              >
                先从左侧选择一个部门或单位。
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

function OperationsSection({ onRefresh }: { onRefresh?: () => Promise<void> | void }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [isRunningReanalyzeAll, setIsRunningReanalyzeAll] = useState(false);
  const aiOptions = useAIAnalysisOptions();
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
    use_local_rules: true,
  };
  if (aiOptions.capabilities) {
    reanalyzeAllRequestBody.use_ai_assist = aiOptions.enabled;
    reanalyzeAllRequestBody.ai_profile_id = aiOptions.enabled ? aiOptions.profileId || null : null;
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
    if (!dryRun && repairPreview?.dry_run !== true) return;
    setIsRepairingLinks(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<LinkRepairPreviewPayload>("/api/jobs/repair-missing-links", {
        dry_run: dryRun,
        only_unassigned: true,
        ...(!dryRun ? { job_ids: (repairPreview?.repairs ?? []).map((item) => String(item.job_id)) } : {}),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setRepairPreview(payload);
      setNotice({
        tone: dryRun ? "info" : payload.failed_count ? "error" : "success",
        message: dryRun
          ? `已预览 ${payload.candidate_count ?? 0} 条待归属记录，尚未保存归属。核对下方建议后点击“正式修复”。`
          : `已保存 ${payload.repaired_count ?? 0} 条报告归属，失败 ${payload.failed_count ?? 0} 条。`,
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
    if (!dryRun && rematchPreview?.dry_run !== true) return;
    setIsRematching(true);
    setNotice(null);
    try {
      const { response, payload } = await postJson<RematchPreviewPayload>("/api/jobs/rematch-organizations", {
        dry_run: dryRun,
        only_unassigned: true,
        ...(!dryRun ? { job_ids: (rematchPreview?.matches ?? []).map((item) => String(item.job_id)) } : {}),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      setRematchPreview(payload);
      setNotice({
        tone: dryRun ? "info" : payload.failed_count ? "error" : "success",
        message: dryRun
          ? `已预览 ${payload.candidate_count ?? 0} 条待归属记录，尚未保存归属。核对下方建议后点击“正式执行”。`
          : `已保存 ${payload.updated_count ?? 0} 条报告归属，失败 ${payload.failed_count ?? 0} 条。`,
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
      <NoticeBanner notice={notice} testId="admin-operations-notice" />
      <Card title="全库报告接入">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <p className="max-w-2xl text-sm leading-6 text-slate-600">上传 PDF 后，系统会自动完成组织匹配、结构化和审校。</p>
          <Button data-testid="admin-operations-open-upload" onClick={() => setIsUploadModalOpen(true)}>
            <UploadCloud className="h-4 w-4" />
            打开上传面板
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="按组织批量重分析">
          <p className="text-sm leading-6 text-slate-600">触发各组织当前最新报告重新分析，并显示实时状态。</p>
          <ReanalyzeAiToggle
            checked={aiOptions.enabled}
            onChange={aiOptions.setEnabled}
            disabled={isRunningReanalyzeAll || !aiOptions.capabilities?.enabled}
            className="mt-4 bg-white"
            testId="admin-reanalyze-ai-toggle"
            description={aiOptions.error || (aiOptions.capabilities?.enabled
              ? aiOptions.enabled
                ? "本次批量重分析将使用系统当前启用的模型；取消勾选后仅执行本地检查。"
                : "模型已配置，但本次默认关闭；勾选后批量重分析会调用大模型。"
              : "当前没有可用的大模型配置，重分析将仅执行本地检查。")}
          />
          <Button
            variant="secondary"
            data-testid="admin-operations-reanalyze-all"
            onClick={() => void runReanalyzeAll()}
            disabled={isRunningReanalyzeAll}
            className="mt-5"
          >
            <RefreshCw className={cn("h-4 w-4", isRunningReanalyzeAll && "animate-spin")} />
            {isRunningReanalyzeAll ? "执行中..." : "开始重分析"}
          </Button>
        </Card>

        <Card title="修复缺失关联">
          <p className="text-sm leading-6 text-slate-600">为“待确认归属”的历史报告补充组织关联。先预览并核对建议，再正式保存。</p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Button variant="secondary" data-testid="admin-operations-repair-preview" onClick={() => void previewRepairLinks(true)} disabled={isRepairingLinks}>
              预览修复
            </Button>
            <Button data-testid="admin-operations-repair-execute" onClick={() => void previewRepairLinks(false)} disabled={isRepairingLinks || repairPreview?.dry_run !== true || !repairPreview.candidate_count}>
              正式修复
            </Button>
          </div>
        </Card>

        <Card title="组织重新匹配">
          <p className="text-sm leading-6 text-slate-600">组织导入后，为“待确认归属”报告重新匹配；已有归属的报告会跳过。预览不会保存归属。</p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Button variant="secondary" data-testid="admin-operations-rematch-preview" onClick={() => void previewRematchOrganizations(true)} disabled={isRematching}>
              预览匹配
            </Button>
            <Button data-testid="admin-operations-rematch-execute" onClick={() => void previewRematchOrganizations(false)} disabled={isRematching || rematchPreview?.dry_run !== true || !rematchPreview.candidate_count}>
              正式执行
            </Button>
          </div>
        </Card>

        <Card title="清理旧结构化版本">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <p className="max-w-xl text-sm leading-6 text-danger-700">只清理数据库中的旧结构化版本，先预览再确认执行。</p>
            <Button
              variant="danger"
              data-testid="admin-operations-cleanup-preview"
              onClick={() => void previewCleanup()}
              disabled={isLoadingCleanupPreview}
            >
              {isLoadingCleanupPreview ? "生成中..." : "开始预览"}
            </Button>
          </div>
        </Card>
      </div>

      {repairPreview ? (
        <Card title="缺失关联修复结果">
          <div className="space-y-4">
            <ResultMetrics testId="admin-operations-repair-results" metrics={[
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
          <div className="space-y-4">
            <ResultMetrics testId="admin-operations-rematch-results" metrics={[
              { label: "候选记录", value: rematchPreview.candidate_count ?? 0 },
              { label: "已更新", value: rematchPreview.updated_count ?? 0 },
              { label: "无封面文字", value: rematchPreview.fast_path_hits ?? 0 },
              { label: "封面文字核验", value: rematchPreview.pdf_text_fallback_hits ?? 0 },
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
          onComplete={(jobIds) => {
            setIsUploadModalOpen(false);
            void onRefresh?.();
            if (jobIds.length === 0) {
              return;
            }
            const target = jobIds.length === 1
              ? `/queue?job=${encodeURIComponent(jobIds[0])}`
              : "/queue";
            window.location.assign(target);
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
    <Card title={title} desc={description}>
      <div className="space-y-4" data-testid={`admin-config-panel-${collection}`}>
        <NoticeBanner notice={notice} testId={`admin-config-notice-${collection}`} />
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="rounded-md border border-border">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <span className="text-sm font-semibold text-slate-900">配置列表</span>
              <Button variant="secondary" size="sm" data-testid={`admin-config-new-${collection}`} onClick={resetForm}>
                新建
              </Button>
            </div>
            <div className="max-h-[520px] overflow-y-auto p-3">
              {loading ? (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载配置...
                </div>
              ) : items.length === 0 ? (
                <div className="rounded-md border border-dashed border-border bg-surface-100 px-4 py-10 text-center text-sm text-slate-500">
                  暂无配置记录。
                </div>
              ) : (
                <div className="space-y-3">
                  {items.map((item) => (
                    <article key={item.id} data-testid={`admin-config-item-${collection}-${item.id}`} className={cn("rounded-md border p-4", selectedId === item.id ? "border-primary-300 bg-primary-50" : "border-border bg-white")}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-slate-900">{item.name}</div>
                          <div className="mt-1 text-sm text-slate-600">{item.description || "没有说明"}</div>
                          <div className="mt-2 text-xs text-slate-500">更新：{formatDate(item.updated_at)} / {item.updated_by}</div>
                        </div>
                        <Badge tone={item.enabled ? "done" : "neutral"}>{item.enabled ? "启用" : "停用"}</Badge>
                      </div>
                      <pre className="mt-3 max-h-36 overflow-auto rounded-md border border-border bg-surface-100 p-3 text-xs leading-5 text-slate-700">
                        {JSON.stringify(item.data, null, 2)}
                      </pre>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button variant="secondary" size="sm" data-testid={`admin-config-edit-${collection}-${item.id}`} onClick={() => editItem(item)}>
                          编辑
                        </Button>
                        <Button variant="secondary" size="sm" data-testid={`admin-config-delete-${collection}-${item.id}`} onClick={() => void deleteItem(item)} disabled={saving} className="border-danger-200 text-danger-700 hover:bg-danger-50">
                          删除
                        </Button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="rounded-md border border-border p-4">
            <h3 className="text-sm font-semibold text-slate-900">{selected ? "编辑配置" : "新建配置"}</h3>
            <div className="mt-4 space-y-3">
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                data-testid={`admin-config-name-${collection}`}
                placeholder="配置名称"
                className={FIELD_CLASS}
              />
              <textarea
                value={descriptionValue}
                onChange={(event) => setDescriptionValue(event.target.value)}
                data-testid={`admin-config-description-${collection}`}
                placeholder="配置说明"
                rows={3}
                className={FIELD_CLASS}
              />
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input type="checkbox" data-testid={`admin-config-enabled-${collection}`} checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
                启用
              </label>
              <textarea
                value={dataText}
                onChange={(event) => setDataText(event.target.value)}
                data-testid={`admin-config-data-${collection}`}
                rows={14}
                spellCheck={false}
                className={cn(FIELD_CLASS, "bg-surface-100 font-mono text-xs leading-5")}
              />
              <div className="flex gap-2">
                <Button
                  data-testid={`admin-config-save-${collection}`}
                  onClick={() => void saveItem()}
                  disabled={saving}
                  className="flex-1"
                >
                  {saving ? "保存中..." : "保存配置"}
                </Button>
                {selected ? (
                  <Button variant="secondary" data-testid={`admin-config-cancel-${collection}`} onClick={resetForm}>
                    取消
                  </Button>
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
      <AIModelSettingsPanel />
      <ConfigPanel
        collection="system-settings"
        title="其他参数留档"
        description="第一版仅保存后台配置记录，不改变运行时环境变量或真实审校逻辑。"
        placeholder={{ key: "备注", value: "", scope: "note" }}
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
  organizationsStatus = "ready",
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

  return (
    <div className="grid gap-5 lg:grid-cols-[208px_minmax(0,1fr)] lg:items-start" data-testid="admin-system-management">
      <nav aria-label="系统设置分区" className="space-y-0.5 rounded-card border border-border bg-white p-2 shadow-soft">
        {SECTIONS.map((section) => {
          const Icon = section.icon;
          const isActive = activeSection === section.id;
          return (
            <button
              key={section.id}
              type="button"
              title={section.description}
              aria-current={isActive ? "true" : undefined}
              data-testid={`admin-section-${section.id}`}
              onClick={() => setActiveSection(section.id)}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400",
                isActive ? "bg-primary-100 text-primary-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0 truncate">{section.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="min-w-0 space-y-5">
        {activeSection === "overview" ? <OverviewSection organizations={organizations} organizationsStatus={organizationsStatus} setSection={setActiveSection} /> : null}
        {activeSection === "organization" ? (
          <OrganizationSection organizations={organizations} onRefresh={onRefresh} />
        ) : null}
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
        {activeSection === "templates" ? <AnnualTemplatePanel /> : null}
        {activeSection === "calendar" ? <DisclosureCalendarPanel /> : null}
        {activeSection === "settings" ? <SettingsTemplatesSection /> : null}
      </div>
    </div>
  );
}
