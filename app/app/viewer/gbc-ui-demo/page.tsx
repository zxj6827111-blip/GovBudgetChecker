"use client";

/* eslint-disable @next/next/no-img-element */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Archive,
  Bell,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Clock,
  Download,
  Eye,
  FileArchive,
  FileText,
  FileUp,
  FolderTree,
  LayoutDashboard,
  ListChecks,
  Loader2,
  LogOut,
  MapPin,
  PackageCheck,
  PlayCircle,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Shield,
  ShieldCheck,
  UploadCloud,
  User,
  UsersRound,
  Wand2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  buildIssueWorkflowKey,
  type IssueWorkflowRecord,
  type IssueWorkflowState,
  type IssueWorkflowStatus,
  type RemediationPackageRecord,
} from "@/lib/issueWorkflowTypes";
import type {
  JobDetailRecord,
  JobSummaryRecord,
  OrganizationRecord,
  StructuredIngestRecord,
} from "@/lib/uiAdapters";
import {
  formatDateTime,
  getDisplayIssueTotal,
  getHighRiskCount,
  normalizeUiTaskStatus,
  toUiProblems,
  toUiTask,
} from "@/lib/uiAdapters";
import type { Problem, Task } from "@/lib/mock";
import SystemManagementPanel from "@/components/admin/SystemManagementPanel";

type PageId = "workbench" | "issues" | "upload" | "archive" | "tasks" | "settings" | "detail";
type Tone = "blue" | "green" | "orange" | "red" | "purple" | "slate";
type ButtonVariant = "default" | "primary" | "green" | "danger" | "dark";
type CurrentUser = { username?: string; is_admin?: boolean };
type UserResponse = { user?: CurrentUser | null };
type OrganizationsResponse = { tree?: OrganizationRecord[] };
type OrganizationsListResponse = { organizations?: OrganizationRecord[] };
type OrganizationJobsResponse = { jobs?: JobSummaryRecord[]; total?: number };
type JobsResponse = JobSummaryRecord[] | { items?: JobSummaryRecord[]; jobs?: JobSummaryRecord[]; total?: number };
type Toast = { id: number; text: string; tone: Tone };
type MaterialSource = {
  report_kind?: JobSummaryRecord["report_kind"];
  doc_type?: string | null;
  filename?: string | null;
  organization_level?: string | null;
  organization_name?: string | null;
};

type LiveIssue = Problem & {
  key: string;
  jobId: string;
  job: JobSummaryRecord;
  task: Task;
  workflow?: IssueWorkflowRecord;
};

type SelectOption = {
  value: string;
  label: string;
};

type SelectedDetail = {
  job: JobSummaryRecord;
  detail: JobDetailRecord;
  task: Task;
  problem: LiveIssue | null;
};

const EMPTY_WORKFLOW: IssueWorkflowState = { issues: {}, packages: [] };
const YEAR_OPTIONS = ["2026", "2025", "2024"];
const ALL_SCOPE_VALUE = "__all__";
const CURRENT_YEAR = String(new Date().getFullYear());
const NAV: Array<[PageId, string, LucideIcon]> = [
  ["workbench", "年度审核", LayoutDashboard],
  ["issues", "问题处理", ListChecks],
  ["upload", "材料上传", FileUp],
  ["archive", "导出归档", Archive],
  ["tasks", "任务中心", ClipboardCheck],
  ["settings", "系统管理", Settings],
];

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const response = await fetch(url, { cache: "no-store", ...init });
    if (!response.ok) return fallback;
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

async function fetchJsonWithAuthState<T>(
  url: string,
  fallback: T,
  init?: RequestInit,
): Promise<{ payload: T; unauthorized: boolean }> {
  try {
    const response = await fetch(url, { cache: "no-store", ...init });
    if (!response.ok) {
      return { payload: fallback, unauthorized: response.status === 401 || response.status === 403 };
    }
    return { payload: (await response.json()) as T, unauthorized: false };
  } catch {
    return { payload: fallback, unauthorized: false };
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text) as Record<string, unknown>;
    return String(payload.detail || payload.error || payload.message || text || `HTTP ${response.status}`);
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function cn(...items: Array<string | false | null | undefined>): string {
  return items.filter(Boolean).join(" ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function normalizeWorkflowState(value: unknown): IssueWorkflowState {
  const raw = isRecord(value) ? value : {};
  const rawIssues = isRecord(raw.issues) ? raw.issues : {};
  const issues: Record<string, IssueWorkflowRecord> = {};

  for (const [key, item] of Object.entries(rawIssues)) {
    if (!isRecord(item)) {
      continue;
    }
    const jobId = String(item.job_id ?? "").trim();
    const issueId = String(item.issue_id ?? "").trim();
    if (!jobId || !issueId) {
      continue;
    }
    const statusText = String(item.status ?? "").trim();
    const status: IssueWorkflowStatus =
      statusText === "confirmed" ||
      statusText === "no_issue" ||
      statusText === "needs_review" ||
      statusText === "in_package"
        ? statusText
        : "pending";
    const resolvedKey = String(item.key ?? key ?? buildIssueWorkflowKey(jobId, issueId)).trim();
    issues[resolvedKey] = {
      key: resolvedKey,
      job_id: jobId,
      issue_id: issueId,
      status,
      title: typeof item.title === "string" ? item.title : undefined,
      severity: typeof item.severity === "string" ? item.severity : undefined,
      page: Number.isFinite(Number(item.page)) ? Number(item.page) : null,
      organization_id: item.organization_id == null ? null : String(item.organization_id),
      organization_name: item.organization_name == null ? null : String(item.organization_name),
      note: typeof item.note === "string" ? item.note : undefined,
      updated_at: String(item.updated_at ?? "") || new Date().toISOString(),
    };
  }

  const packages = Array.isArray(raw.packages)
    ? raw.packages
        .filter(isRecord)
        .map((item, index) => {
          const statusText = String(item.status ?? "").trim();
          const status: RemediationPackageRecord["status"] =
            statusText === "ready" || statusText === "submitted" ? statusText : "draft";
          const createdAt = String(item.created_at ?? "") || new Date().toISOString();
          return {
            id: String(item.id ?? `pkg-${index}`).trim() || `pkg-${index}`,
            name: String(item.name ?? "").trim() || "未命名整改包",
            organization_id: item.organization_id == null ? null : String(item.organization_id),
            organization_name: item.organization_name == null ? null : String(item.organization_name),
            job_ids: normalizeStringArray(item.job_ids),
            issue_keys: normalizeStringArray(item.issue_keys),
            status,
            created_at: createdAt,
            updated_at: String(item.updated_at ?? "") || createdAt,
          };
        })
    : [];

  return {
    issues,
    packages,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : undefined,
  };
}

function collectOrgs(nodes: OrganizationRecord[]): OrganizationRecord[] {
  return nodes.flatMap((node) => [node, ...collectOrgs(node.children ?? [])]);
}

function findOrg(nodes: OrganizationRecord[], id: string): OrganizationRecord | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const child = findOrg(node.children ?? [], id);
    if (child) return child;
  }
  return null;
}

function firstUsefulOrg(nodes: OrganizationRecord[]): OrganizationRecord | null {
  const all = collectOrgs(nodes);
  return (
    all.find((item) => item.level === "unit" && Number(item.job_count ?? 0) > 0) ??
    all.find((item) => Number(item.job_count ?? 0) > 0) ??
    all.find((item) => item.level === "unit") ??
    all.find((item) => item.level === "department") ??
    all[0] ??
    null
  );
}

function filterOrganizations(nodes: OrganizationRecord[], query: string, filter: string): OrganizationRecord[] {
  const normalized = query.trim().toLowerCase();
  return nodes.flatMap((node) => {
    const children = filterOrganizations(node.children ?? [], query, filter);
    const issueCount = Number(node.issue_count ?? 0);
    const jobCount = Number(node.job_count ?? 0);
    const matchesText = !normalized || node.name.toLowerCase().includes(normalized) || displayOrgName(node.name, node.level).toLowerCase().includes(normalized);
    const matchesFilter =
      filter === "全部" ||
      (filter === "有问题" && issueCount > 0) ||
      (filter === "未上传" && jobCount === 0) ||
      (filter === "待整改" && issueCount > 0) ||
      (filter === "高风险" && issueCount > 0);
    if ((!matchesText || !matchesFilter) && children.length === 0) return [];
    return [{ ...node, children }];
  });
}

function normalizeJobsPayload(payload: JobsResponse): JobSummaryRecord[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.items)) return payload.items;
  if (Array.isArray(payload.jobs)) return payload.jobs;
  return [];
}

function sortedJobs(jobs: JobSummaryRecord[]): JobSummaryRecord[] {
  return [...jobs].sort((a, b) => Number(b.updated_ts ?? b.ts ?? 0) - Number(a.updated_ts ?? a.ts ?? 0));
}

function timestampYear(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "";
  }

  const millis = numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
  const year = new Date(millis).getFullYear();
  return year >= 2000 && year <= Number(CURRENT_YEAR) + 1 ? String(year) : "";
}

function jobYear(job: JobSummaryRecord): string {
  const direct = Number(job.report_year);
  if (Number.isFinite(direct) && direct >= 2000 && direct <= Number(CURRENT_YEAR) + 1) {
    return String(direct);
  }

  const values = [job.filename, job.doc_type];
  for (const value of values) {
    const text = String(value ?? "");
    const match = text.match(/20\d{2}/);
    if (match) {
      return match[0];
    }
  }
  return timestampYear(job.updated_ts) || timestampYear(job.created_ts) || timestampYear(job.ts);
}

function matchesMaterialFilter(job: JobSummaryRecord, filter: string): boolean {
  if (filter === "all") return true;
  const kind = reportKindLabel(job);
  const subject = materialSubjectLabel(job);
  if (filter === "budget") return kind === "预算";
  if (filter === "final") return kind === "决算";
  if (filter === "department") return subject === "部门";
  if (filter === "unit") return subject === "单位";
  return true;
}

function matchesJobStatusFilter(job: JobSummaryRecord, filter: string): boolean {
  if (filter === "all") return true;
  return normalizeUiTaskStatus(job.status) === filter;
}

function matchesWorkflowFilter(issue: LiveIssue | undefined, filter: string): boolean {
  if (filter === "all") return true;
  if (filter === "pending") return !issue?.workflow || issue.workflow.status === "pending";
  if (filter === "confirmed") return issue?.workflow?.status === "confirmed";
  if (filter === "in_package") return issue?.workflow?.status === "in_package";
  if (filter === "handled") return Boolean(issue?.workflow && issue.workflow.status !== "pending");
  if (filter === "no_issue") return issue?.workflow?.status === "no_issue";
  return true;
}

function buildYearOptions(jobs: JobSummaryRecord[], includeAll = false): SelectOption[] {
  const years = Array.from(new Set([...YEAR_OPTIONS, CURRENT_YEAR, ...jobs.map(jobYear).filter(Boolean)]))
    .sort((left, right) => Number(right) - Number(left));
  const options = years.map((year) => ({ value: year, label: `${year} 年度` }));
  return includeAll ? [{ value: "all", label: "全部年度" }, ...options] : options;
}

function statusText(status: string | undefined): string {
  const normalized = normalizeUiTaskStatus(status);
  if (normalized === "completed") return "已完成";
  if (normalized === "failed") return "失败";
  return "执行中";
}

function statusTone(status: string | undefined): Tone {
  const normalized = normalizeUiTaskStatus(status);
  if (normalized === "completed") return "green";
  if (normalized === "failed") return "red";
  return "blue";
}

function workflowLabel(status?: IssueWorkflowStatus): string {
  if (status === "confirmed") return "待复核";
  if (status === "no_issue") return "已确认无问题";
  if (status === "needs_review") return "退回人工复核";
  if (status === "in_package") return "已加入整改包";
  return "待处理";
}

function workflowTone(status?: IssueWorkflowStatus): Tone {
  if (status === "confirmed") return "orange";
  if (status === "no_issue") return "green";
  if (status === "needs_review") return "purple";
  if (status === "in_package") return "blue";
  return "slate";
}

function severityTone(value: string | undefined): Tone {
  const text = String(value ?? "").toLowerCase();
  if (["critical", "high", "error"].some((item) => text.includes(item))) return "red";
  if (["medium", "warn", "manual"].some((item) => text.includes(item))) return "orange";
  if (["low", "info"].some((item) => text.includes(item))) return "blue";
  return "slate";
}

function isHighRisk(value: string | undefined): boolean {
  return severityTone(value) === "red";
}

function reportKindLabel(job: MaterialSource): string {
  if (job.report_kind === "final") return "决算";
  if (job.report_kind === "budget") return "预算";
  const text = String(job.doc_type ?? "");
  if (text.includes("final") || text.includes("决算")) return "决算";
  if (text.includes("budget") || text.includes("预算")) return "预算";
  return "待识别";
}

function displayOrgName(value?: string | null, level?: string | null): string {
  const original = String(value ?? "").trim();
  if (!original) return "未关联单位";
  if (original === "上海市" || original === "普陀区") return original;

  let name = original;
  if (name.startsWith("上海市普陀区")) {
    name = name.slice("上海市普陀区".length).trim();
  }
  if (!name) return "普陀区";

  const streetOrTown = /街道|镇/.test(name);
  const districtPrefixes = [
    "规划", "财政", "教育", "公安", "民政", "司法", "人力", "生态", "建设", "交通", "水务",
    "文化", "卫生", "退役", "应急", "审计", "市场", "体育", "统计", "医保", "绿化", "城市",
    "发展", "商务", "科委", "档案", "国资", "残疾", "妇女", "大数据", "机关",
  ];
  const likelyDistrictAgency =
    !streetOrTown &&
    (level === "department" ||
      name.endsWith("本级") ||
      districtPrefixes.some((prefix) => name.startsWith(prefix)) ||
      /(局|委员会|办公室|联合会|法院|检察院|党校)$/.test(name));

  if (original.startsWith("上海市普陀区") && likelyDistrictAgency && !name.startsWith("区")) {
    return `区${name}`;
  }
  return name;
}

function orgLevelLabel(level?: string | null): string {
  if (level === "department") return "部门";
  if (level === "unit") return "单位";
  if (level === "district") return "区级";
  if (level === "city") return "市级";
  return "组织";
}

function selectedOrgName(org: OrganizationRecord | null): string {
  return org ? displayOrgName(org.name, org.level) : "全部单位";
}

function jobOrgName(job: MaterialSource): string {
  return displayOrgName(job.organization_name, job.organization_level);
}

function materialSubjectLabel(job: MaterialSource): string {
  const filename = String(job.filename ?? "");
  const docType = String(job.doc_type ?? "").toLowerCase();
  if (/单位预算|单位决算|单位公开|单位/.test(filename)) return "单位";
  if (/部门预算|部门决算|部门公开|（部门）|\(部门\)|部门/.test(filename)) return "部门";
  if (/unit|单位/.test(docType)) return "单位";
  if (/dept|department|部门/.test(docType)) return "部门";
  if (/本级/.test(filename)) return "单位";
  if (job.organization_level === "department") return "部门";
  if (job.organization_level === "unit") return "单位";
  return "主体待识别";
}

function materialTypeLabel(job: MaterialSource): string {
  const subject = materialSubjectLabel(job);
  const kind = reportKindLabel(job);
  if (subject === "主体待识别") return kind;
  if (kind === "待识别") return `${subject}材料`;
  return `${subject}${kind}`;
}

function getJobProgress(job: JobSummaryRecord): number {
  const direct = Number(job.progress);
  if (Number.isFinite(direct)) return Math.max(0, Math.min(100, Math.round(direct)));
  return normalizeUiTaskStatus(job.status) === "completed" ? 100 : normalizeUiTaskStatus(job.status) === "failed" ? 100 : 35;
}

function buildIssuesFromDetails(details: JobDetailRecord[], jobs: JobSummaryRecord[], workflow: IssueWorkflowState): LiveIssue[] {
  const jobsById = new Map(jobs.map((job) => [job.job_id, job]));
  return details.flatMap((detail) => {
    const job = jobsById.get(detail.job_id) ?? detail;
    const task = toUiTask({ ...job, ...detail });
    return toUiProblems(detail).map((problem) => {
      const key = buildIssueWorkflowKey(detail.job_id, problem.id);
      return { ...problem, key, jobId: detail.job_id, job, task, workflow: workflow.issues[key] };
    });
  });
}

function downloadUrl(jobId: string, format: "pdf" | "json" = "pdf"): string {
  return `/api/reports/download?job_id=${encodeURIComponent(jobId)}&format=${format}`;
}

function sourceUrl(jobId: string): string {
  return `/api/files/${encodeURIComponent(jobId)}/source`;
}

function previewUrl(jobId: string): string {
  return `/api/files/${encodeURIComponent(jobId)}/preview`;
}

function Card({ title, desc, action, className, children }: { title?: string; desc?: string; action?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <section className={cn("rounded-lg border border-slate-200 bg-white shadow-sm", className)}>
      {(title || desc || action) && (
        <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            {title && <h3 className="text-lg font-black text-slate-900">{title}</h3>}
            {desc && <p className="mt-1 text-sm text-slate-500">{desc}</p>}
          </div>
          {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

function Button({ children, onClick, disabled, variant = "default", className, type = "button" }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean; variant?: ButtonVariant; className?: string; type?: "button" | "submit" }) {
  const variantClass =
    variant === "primary"
      ? "border-blue-600 bg-blue-600 text-white hover:bg-blue-700"
      : variant === "green"
        ? "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700"
        : variant === "danger"
          ? "border-red-200 bg-red-50 text-red-600 hover:bg-red-100"
          : variant === "dark"
            ? "border-slate-900 bg-slate-900 text-white hover:bg-slate-800"
            : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn("inline-flex h-10 items-center justify-center gap-2 rounded-md border px-4 text-sm font-bold transition disabled:cursor-not-allowed disabled:opacity-50", variantClass, className)}
    >
      {children}
    </button>
  );
}

function Pill({ tone = "slate", children }: { tone?: Tone; children: React.ReactNode }) {
  const cls =
    tone === "blue"
      ? "bg-blue-50 text-blue-700 ring-blue-100"
      : tone === "green"
        ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
        : tone === "orange"
          ? "bg-orange-50 text-orange-700 ring-orange-100"
          : tone === "red"
            ? "bg-red-50 text-red-700 ring-red-100"
            : tone === "purple"
              ? "bg-violet-50 text-violet-700 ring-violet-100"
              : "bg-slate-50 text-slate-600 ring-slate-100";
  return <span className={cn("inline-flex items-center rounded-md px-2 py-1 text-xs font-black ring-1", cls)}>{children}</span>;
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-3 text-xs font-black text-slate-500">{children}</th>;
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-4 py-3 align-top text-sm text-slate-700", className)}>{children}</td>;
}

function Metric({ icon: Icon, label, value, desc, tone }: { icon: LucideIcon; label: string; value: string | number; desc: string; tone: Tone }) {
  const cls = tone === "green" ? "bg-emerald-500" : tone === "orange" ? "bg-orange-500" : tone === "red" ? "bg-red-500" : tone === "purple" ? "bg-violet-600" : "bg-blue-600";
  return (
    <Card>
      <div className="flex min-h-[116px] items-center gap-4 p-5">
        <span className={cn("flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-white", cls)}><Icon className="h-6 w-6" /></span>
        <div className="min-w-0">
          <div className="text-sm text-slate-500">{label}</div>
          <div className="mt-1 text-3xl font-black tracking-tight text-slate-950">{value}</div>
          <div className="mt-1 text-xs text-slate-500">{desc}</div>
        </div>
      </div>
    </Card>
  );
}

function Header({ page, setPage, user, onLogout }: { page: PageId; setPage: (page: PageId) => void; user: CurrentUser | null; onLogout: () => void }) {
  const [noticeOpen, setNoticeOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const notices = [
    { label: "待处理问题", value: "查看问题", target: "issues" as PageId },
    { label: "任务中心", value: "查看任务", target: "tasks" as PageId },
    { label: "导出归档", value: "生成整改包", target: "archive" as PageId },
  ];

  const openNoticeTarget = (target: PageId) => {
    setNoticeOpen(false);
    setUserMenuOpen(false);
    setPage(target);
  };

  return (
    <header className="sticky top-0 z-40 h-[70px] bg-gradient-to-r from-blue-800 via-blue-700 to-blue-900 text-white shadow-md">
      <div className="flex h-full items-center">
        <button type="button" onClick={() => setPage("workbench")} className="flex w-[300px] shrink-0 items-center gap-3 px-7 text-left">
          <Shield className="h-11 w-11" />
          <div>
            <div className="text-xl font-black">GovBudgetChecker</div>
            <div className="text-sm font-semibold opacity-95">预算决算公开审核系统</div>
          </div>
        </button>
        <nav className="flex h-full flex-1 items-center justify-center gap-1">
          {NAV.map(([id, label, Icon]) => (
            <button key={id} type="button" onClick={() => setPage(id)} className={cn("relative flex h-full min-w-[122px] items-center justify-center gap-2 px-5 text-base font-bold transition", page === id ? "bg-white/12" : "hover:bg-white/8")}>
              <Icon className="h-5 w-5" />
              {label}
              {page === id && <span className="absolute bottom-0 left-1/2 h-1 w-10 -translate-x-1/2 rounded-full bg-white" />}
            </button>
          ))}
        </nav>
        <div className="flex h-full shrink-0 items-center gap-5 px-7">
          <div className="relative">
            <button
              type="button"
              aria-label="打开消息提醒"
              onClick={() => {
                setUserMenuOpen(false);
                setNoticeOpen((current) => !current);
              }}
              className="relative rounded-md p-2 transition hover:bg-white/10"
            >
              <Bell className="h-6 w-6" />
              <span className="absolute -right-1 -top-1 rounded-full bg-red-500 px-2 py-0.5 text-xs font-bold">12</span>
            </button>
            {noticeOpen ? (
              <div className="absolute right-0 top-[calc(100%+14px)] w-72 overflow-hidden rounded-lg border border-slate-200 bg-white text-slate-900 shadow-xl">
                <div className="border-b border-slate-100 px-4 py-3">
                  <div className="text-sm font-black">消息提醒</div>
                  <div className="mt-1 text-xs text-slate-500">系统把待处理事项集中到这里。</div>
                </div>
                <div className="divide-y divide-slate-100">
                  {notices.map((notice) => (
                    <button
                      key={notice.target}
                      type="button"
                      onClick={() => openNoticeTarget(notice.target)}
                      className="flex w-full items-center justify-between px-4 py-3 text-left text-sm transition hover:bg-slate-50"
                    >
                      <span className="font-bold">{notice.label}</span>
                      <span className="text-xs font-bold text-blue-600">{notice.value}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
          <div className="relative border-l border-white/25 pl-4">
            <button
              type="button"
              aria-label="打开用户菜单"
              onClick={() => {
                setNoticeOpen(false);
                setUserMenuOpen((current) => !current);
              }}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 transition hover:bg-white/10"
            >
              <User className="h-8 w-8 rounded-full bg-white/90 p-1 text-blue-700" />
              <span className="font-bold">{user?.username || "审核员"}</span>
              <ChevronDown className={cn("h-4 w-4 transition", userMenuOpen && "rotate-180")} />
            </button>
            {userMenuOpen ? (
              <div className="absolute right-0 top-[calc(100%+14px)] w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-slate-900 shadow-xl">
                <button
                  type="button"
                  onClick={() => {
                    setUserMenuOpen(false);
                    setPage("settings");
                  }}
                  className="block w-full px-4 py-2 text-left text-sm font-bold transition hover:bg-slate-50"
                >
                  系统管理
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setUserMenuOpen(false);
                    window.location.href = "/account/password";
                  }}
                  className="block w-full px-4 py-2 text-left text-sm font-bold transition hover:bg-slate-50"
                >
                  修改密码
                </button>
              </div>
            ) : null}
          </div>
          <button type="button" onClick={onLogout} className="flex items-center gap-2 border-l border-white/25 pl-4 font-bold">
            <LogOut className="h-4 w-4" />
            退出
          </button>
        </div>
      </div>
    </header>
  );
}

function OrgNode({ node, depth, selectedId, expanded, forceOpen, onToggle, onSelect }: { node: OrganizationRecord; depth: number; selectedId: string; expanded: Record<string, boolean>; forceOpen: boolean; onToggle: (id: string) => void; onSelect: (node: OrganizationRecord) => void }) {
  const hasChildren = Boolean(node.children?.length);
  const isOpen = forceOpen || Boolean(expanded[node.id]);
  const selected = selectedId === node.id;
  const issueCount = Number(node.issue_count ?? 0);
  const shortName = displayOrgName(node.name, node.level);
  return (
    <div>
      <div className={cn("group flex items-center gap-2 rounded-md px-2 py-2 text-sm font-bold text-slate-700 hover:bg-blue-50", selected && "bg-blue-50 text-blue-700")} style={{ paddingLeft: 12 + depth * 16 }}>
        <button type="button" onClick={() => (hasChildren ? onToggle(node.id) : onSelect(node))} className="flex h-5 w-5 items-center justify-center text-slate-500">
          {hasChildren ? <ChevronRight className={cn("h-4 w-4 transition", isOpen && "rotate-90")} /> : <span className="h-4 w-4" />}
        </button>
        <button type="button" onClick={() => onSelect(node)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          {node.level === "unit" ? <UsersRound className="h-4 w-4 text-slate-400" /> : <Building2 className="h-4 w-4 text-slate-400" />}
          <span className="truncate" title={node.name}>{shortName}</span>
          {(node.level === "department" || node.level === "unit") && <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500">{orgLevelLabel(node.level)}</span>}
        </button>
        {issueCount > 0 && <span className="rounded-full bg-red-500 px-2 py-0.5 text-xs text-white">{issueCount}</span>}
      </div>
      {hasChildren && isOpen && (
        <div>
          {(node.children ?? []).map((child) => (
            <OrgNode key={child.id} node={child} depth={depth + 1} selectedId={selectedId} expanded={expanded} forceOpen={forceOpen} onToggle={onToggle} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

function Sidebar({ orgTree, selectedOrg, onSelectOrg }: { orgTree: OrganizationRecord[]; selectedOrg: OrganizationRecord | null; onSelectOrg: (node: OrganizationRecord) => void }) {
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setExpanded({});
  }, [orgTree]);

  const visibleTree = useMemo(() => filterOrganizations(orgTree, query, filter), [filter, orgTree, query]);
  const forceOpen = Boolean(query.trim());

  if (collapsed) {
    return (
      <aside className="h-[calc(100vh-70px)] w-[72px] shrink-0 border-r border-slate-200 bg-white p-4">
        <button type="button" onClick={() => setCollapsed(false)} className="flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 text-blue-600">
          <FolderTree className="h-5 w-5" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="h-[calc(100vh-70px)] w-[300px] shrink-0 overflow-hidden border-r border-slate-200 bg-white">
      <div className="flex h-full flex-col">
        <div className="p-5">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-lg font-black">行政区划与单位</h2>
            <button type="button" onClick={() => setCollapsed(true)} className="flex items-center gap-1 text-sm font-bold text-blue-600">
              <ChevronLeft className="h-4 w-4" />收起
            </button>
          </div>
          <div className="flex h-10 items-center rounded-md border border-slate-200 px-3">
            <input value={query} onChange={(event) => setQuery(event.target.value)} className="w-full text-sm outline-none" placeholder="搜索单位名称" />
            <Search className="h-4 w-4 text-slate-400" />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {["全部", "有问题", "未上传", "待整改", "高风险"].map((item) => (
              <button key={item} type="button" onClick={() => setFilter(item)} className={cn("rounded-md border px-3 py-1.5 text-sm font-bold", filter === item ? "border-blue-500 bg-blue-50 text-blue-700" : item === "高风险" ? "border-red-100 bg-red-50 text-red-600" : "border-slate-200 bg-white text-slate-700")}>
                {item}
              </button>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain border-t border-slate-100 p-4">
          {visibleTree.map((node) => (
            <OrgNode key={node.id} node={node} depth={0} selectedId={selectedOrg?.id ?? ""} expanded={expanded} forceOpen={forceOpen} onToggle={(id) => setExpanded((current) => ({ ...current, [id]: !current[id] }))} onSelect={onSelectOrg} />
          ))}
        </div>
        <div className="border-t border-slate-100 p-5 text-sm text-slate-500">共 {collectOrgs(orgTree).filter((item) => item.level === "unit").length} 个单位</div>
      </div>
    </aside>
  );
}

function PageTitle({ selectedOrg, title, subtitle, action }: { selectedOrg: OrganizationRecord | null; title: string; subtitle: string; action?: React.ReactNode }) {
  return (
    <div className="mb-6">
      <div className="mb-3 flex items-center gap-3 text-sm text-slate-500">
        <span>上海市</span><span>/</span><span>普陀区</span><span>/</span><span title={selectedOrg?.name}>{selectedOrgName(selectedOrg)}</span>
      </div>
      <div className="flex items-start justify-between gap-5">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-slate-950">{title}</h1>
          <p className="mt-2 text-sm font-medium text-slate-600">{subtitle}</p>
        </div>
        {action}
      </div>
    </div>
  );
}

function SelectBlock({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium text-slate-600">{label}</span>
      <span className="relative block">
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="h-12 w-full appearance-none rounded-md border border-slate-200 bg-white py-2 pl-3 pr-10 text-sm font-bold text-slate-900 outline-none transition-colors hover:border-blue-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
      </span>
    </label>
  );
}

function PaginationFooter({ total, label = "条" }: { total: number; label?: string }) {
  return (
    <div className="flex items-center justify-between border-t border-slate-100 px-5 py-4 text-sm text-slate-500">
      <span>共 {total} {label}</span>
      <div className="flex items-center gap-2">
        <Button className="h-8 w-8 px-0"><ChevronLeft className="h-4 w-4" /></Button>
        <Button variant="primary" className="h-8 w-8 px-0">1</Button>
        <Button className="h-8 w-8 px-0"><ChevronRight className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}

function TodoLine({ tone, title, value, text, setPage, target }: { tone: Tone; title: string; value: string; text: string; setPage: (page: PageId) => void; target: PageId }) {
  const cls = tone === "red" ? "border-red-100 bg-red-50 text-red-600" : tone === "orange" ? "border-orange-100 bg-orange-50 text-orange-600" : "border-violet-100 bg-violet-50 text-violet-600";
  return (
    <button type="button" onClick={() => setPage(target)} className={cn("flex w-full items-center justify-between rounded-md border p-3 text-left", cls)}>
      <span><b>{title}</b><span className="ml-2 text-xl font-black">{value}</span><p className="text-xs opacity-80">{text}</p></span>
      <ChevronRight className="h-4 w-4" />
    </button>
  );
}

function Workbench({
  selectedOrg,
  orgOptions,
  scopeValue,
  onScopeChange,
  jobs,
  issues,
  year,
  onYearChange,
  setPage,
  onSelectProblem,
}: {
  selectedOrg: OrganizationRecord | null;
  orgOptions: SelectOption[];
  scopeValue: string;
  onScopeChange: (value: string) => void;
  jobs: JobSummaryRecord[];
  issues: LiveIssue[];
  year: string;
  onYearChange: (value: string) => void;
  setPage: (page: PageId) => void;
  onSelectProblem: (issue: LiveIssue) => void;
}) {
  const [materialFilter, setMaterialFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [packageFilter, setPackageFilter] = useState("all");
  const issueByJobId = useMemo(() => {
    const map = new Map<string, LiveIssue[]>();
    for (const issue of issues) {
      map.set(issue.jobId, [...(map.get(issue.jobId) ?? []), issue]);
    }
    return map;
  }, [issues]);
  const filteredJobs = useMemo(() => jobs.filter((job) => {
    const jobIssues = issueByJobId.get(job.job_id) ?? [];
    if (year !== "all" && jobYear(job) !== year) return false;
    if (!matchesMaterialFilter(job, materialFilter)) return false;
    if (!matchesJobStatusFilter(job, statusFilter)) return false;
    if (packageFilter === "pending" && jobIssues.every((issue) => issue.workflow?.status !== "confirmed")) return false;
    if (packageFilter === "in_package" && jobIssues.every((issue) => issue.workflow?.status !== "in_package")) return false;
    if (packageFilter === "not_in_package" && jobIssues.some((issue) => issue.workflow?.status === "in_package")) return false;
    return true;
  }), [issueByJobId, jobs, materialFilter, packageFilter, statusFilter, year]);
  const visibleIssues = useMemo(() => {
    const visibleJobIds = new Set(filteredJobs.map((job) => job.job_id));
    return issues.filter((issue) => visibleJobIds.has(issue.jobId));
  }, [filteredJobs, issues]);
  const completed = filteredJobs.filter((job) => normalizeUiTaskStatus(job.status) === "completed").length;
  const highRisk = visibleIssues.filter((issue) => isHighRisk(issue.severity)).length;
  const pending = visibleIssues.filter((issue) => !issue.workflow || issue.workflow.status === "pending").length;
  const departmentMaterials = filteredJobs.filter((job) => materialSubjectLabel(job) === "部门").length;
  const unitMaterials = filteredJobs.filter((job) => materialSubjectLabel(job) === "单位").length;
  const rows = filteredJobs.slice(0, 8);
  const scopeName = selectedOrgName(selectedOrg);
  const yearOptions = useMemo(() => buildYearOptions(jobs, true), [jobs]);

  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title={`${scopeName} · 年度审核工作台`} subtitle="用于预算、决算公开材料的审核、问题处理与整改跟踪，数据来自本地真实任务和组织库。" action={<Button onClick={() => setPage("tasks")}><RefreshCw className="h-4 w-4" />任务中心</Button>} />
      <Card className="mb-5">
        <div className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_auto] gap-8 px-7 py-5">
          <SelectBlock label="检查年度" value={year} onChange={onYearChange} options={yearOptions} />
          <SelectBlock label="当前范围" value={scopeValue} onChange={onScopeChange} options={orgOptions} />
          <SelectBlock
            label="材料类型"
            value={materialFilter}
            onChange={setMaterialFilter}
            options={[
              { value: "all", label: "全部" },
              { value: "budget", label: "预算" },
              { value: "final", label: "决算" },
              { value: "department", label: "部门材料" },
              { value: "unit", label: "单位材料" },
            ]}
          />
          <SelectBlock
            label="处理状态"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: "all", label: "全部状态" },
              { value: "completed", label: "已完成" },
              { value: "analyzing", label: "执行中" },
              { value: "failed", label: "失败" },
            ]}
          />
          <SelectBlock
            label="客户整改包"
            value={packageFilter}
            onChange={setPackageFilter}
            options={[
              { value: "all", label: "全部" },
              { value: "pending", label: "待提交优先" },
              { value: "in_package", label: "已加入整改包" },
              { value: "not_in_package", label: "未加入整改包" },
            ]}
          />
          <Button onClick={() => { onYearChange(CURRENT_YEAR); onScopeChange(ALL_SCOPE_VALUE); setMaterialFilter("all"); setStatusFilter("all"); setPackageFilter("all"); }}>
            <RefreshCw className="h-4 w-4" />重置
          </Button>
        </div>
      </Card>
      <div className="mb-5 grid grid-cols-5 gap-4">
        <Metric icon={Building2} label="范围内任务" value={`${jobs.length}份`} desc={`部门 ${departmentMaterials} / 单位 ${unitMaterials}`} tone="blue" />
        <Metric icon={FileText} label="已完成材料" value={`${completed}份`} desc="已完成分析或复核" tone="green" />
        <Metric icon={Clock} label="待确认问题" value={`${pending}个`} desc="需要审核员处理" tone="orange" />
        <Metric icon={ShieldCheck} label="高风险问题" value={`${highRisk}个`} desc="需优先核查" tone="red" />
        <Card title="今日待办">
          <div className="space-y-3 p-4">
            <TodoLine tone="red" title="先处理高风险" value={`${highRisk}个`} text="高风险问题待处理" setPage={setPage} target="issues" />
            <TodoLine tone="orange" title="待确认问题" value={`${pending}个`} text="问题需要复核确认" setPage={setPage} target="issues" />
            <TodoLine tone="purple" title="生成整改包" value={`${visibleIssues.filter((issue) => issue.workflow?.status === "confirmed").length}个`} text="已确认问题可打包" setPage={setPage} target="archive" />
          </div>
        </Card>
      </div>
      <div className="grid grid-cols-[1fr_300px] gap-5">
        <Card title="单位材料与问题清单">
          <table className="w-full text-left">
            <thead className="bg-slate-50"><tr><Th>单位 / 材料</Th><Th>类型</Th><Th>任务状态</Th><Th>问题</Th><Th>高风险</Th><Th>整改状态</Th><Th>操作</Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {rows.length === 0 ? (
                <tr><Td>当前范围暂无任务，请先上传材料。</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td></tr>
              ) : rows.map((job) => {
                const jobIssues = issues.filter((issue) => issue.jobId === job.job_id);
                const firstIssue = jobIssues[0];
                return (
                  <tr key={job.job_id}>
                    <Td><b title={job.organization_name ?? undefined}>{jobOrgName(job)}</b><div className="mt-1 line-clamp-1 text-xs text-slate-500">{toUiTask(job).filename}</div></Td>
                    <Td><Pill tone={materialSubjectLabel(job) === "部门" ? "purple" : "blue"}>{materialTypeLabel(job)}</Pill></Td>
                    <Td><Pill tone={statusTone(job.status)}>{statusText(job.status)}</Pill></Td>
                    <Td>{getDisplayIssueTotal(job)}</Td>
                    <Td className="text-red-600">{getHighRiskCount(job)}</Td>
                    <Td><Pill tone={firstIssue ? workflowTone(firstIssue.workflow?.status) : "green"}>{firstIssue ? workflowLabel(firstIssue.workflow?.status) : "无问题"}</Pill></Td>
                    <Td>
                      <div className="flex flex-wrap gap-3 font-bold text-blue-600">
                        <button type="button" onClick={() => { if (firstIssue) onSelectProblem(firstIssue); setPage("issues"); }}>查看问题</button>
                        <button type="button" onClick={() => setPage("upload")}>上传材料</button>
                        <button type="button" onClick={() => { if (firstIssue) onSelectProblem(firstIssue); setPage("detail"); }}>进入详情</button>
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <PaginationFooter total={rows.length} />
        </Card>
        <Card title="工作提醒" action={<button type="button" onClick={() => setPage("issues")} className="text-sm font-bold text-blue-600">更多</button>}>
          <ul className="space-y-4 p-5 text-sm leading-6 text-slate-600">
            <li><b className="text-slate-900">优先处理高风险问题</b><br />高风险问题影响公开合规性，请优先确认。</li>
            <li><b className="text-slate-900">关注缺失材料</b><br />缺失材料会影响整改包生成，请提醒单位补齐。</li>
            <li><b className="text-slate-900">按版本处理材料</b><br />单位重新上传后，建议以最新任务为准。</li>
            <li><b className="text-slate-900">及时提交整改包</b><br />确认后的问题可以进入导出归档。</li>
          </ul>
        </Card>
      </div>
    </>
  );
}

function Explain({ tone, title, desc }: { tone: Tone; title: string; desc: string }) {
  const Icon = tone === "green" ? CheckCircle2 : tone === "orange" ? Clock : tone === "purple" ? PackageCheck : ShieldCheck;
  return (
    <div className="flex items-start gap-3">
      <span className={cn("mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full", tone === "green" ? "bg-emerald-100 text-emerald-600" : tone === "orange" ? "bg-orange-100 text-orange-600" : tone === "purple" ? "bg-violet-100 text-violet-600" : "bg-slate-100 text-slate-500")}><Icon className="h-4 w-4" /></span>
      <span><b>{title}</b><p className="text-sm text-slate-500">{desc}</p></span>
    </div>
  );
}

function Issues({
  selectedOrg,
  orgOptions,
  scopeValue,
  onScopeChange,
  jobs,
  issues,
  year,
  onYearChange,
  selectedIssue,
  setPage,
  onSelectProblem,
  onWorkflow,
  onBatchWorkflow,
  selectedIssueKeys,
  setSelectedIssueKeys,
  operationBusy,
}: {
  selectedOrg: OrganizationRecord | null;
  orgOptions: SelectOption[];
  scopeValue: string;
  onScopeChange: (value: string) => void;
  jobs: JobSummaryRecord[];
  issues: LiveIssue[];
  year: string;
  onYearChange: (value: string) => void;
  selectedIssue: LiveIssue | null;
  setPage: (page: PageId) => void;
  onSelectProblem: (issue: LiveIssue) => void;
  onWorkflow: (issue: LiveIssue, status: IssueWorkflowStatus) => Promise<void>;
  onBatchWorkflow: (status: IssueWorkflowStatus) => Promise<void>;
  selectedIssueKeys: string[];
  setSelectedIssueKeys: (keys: string[]) => void;
  operationBusy: boolean;
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const yearOptions = useMemo(() => buildYearOptions(jobs, true), [jobs]);
  const typeOptions = useMemo<SelectOption[]>(() => {
    const categories = Array.from(new Set(issues.map((issue) => String(issue.category || issue.ruleId || "").trim()).filter(Boolean)))
      .sort((left, right) => left.localeCompare(right, "zh-CN"));
    return [{ value: "all", label: "全部" }, ...categories.map((item) => ({ value: item, label: item }))];
  }, [issues]);
  const visibleIssues = useMemo(() => issues.filter((issue) => {
    if (year !== "all" && jobYear(issue.job) !== year) return false;
    if (!matchesWorkflowFilter(issue, statusFilter)) return false;
    if (severityFilter === "high" && !isHighRisk(issue.severity)) return false;
    if (severityFilter === "medium" && severityTone(issue.severity) !== "orange") return false;
    if (severityFilter === "low" && !["blue", "slate"].includes(severityTone(issue.severity))) return false;
    if (typeFilter !== "all" && String(issue.category || issue.ruleId || "") !== typeFilter) return false;
    if (!normalized) return true;
    return [issue.title, issue.ruleId, issue.task.filename, issue.task.department, jobOrgName(issue.job), materialTypeLabel(issue.job)].join(" ").toLowerCase().includes(normalized);
  }), [issues, normalized, severityFilter, statusFilter, typeFilter, year]);
  const grouped = useMemo(() => {
    const map = new Map<string, LiveIssue[]>();
    for (const issue of visibleIssues) {
      const key = jobOrgName(issue.job);
      map.set(key, [...(map.get(key) ?? []), issue]);
    }
    return Array.from(map.entries());
  }, [visibleIssues]);
  const scopeName = selectedOrgName(selectedOrg);

  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title="问题处理台" subtitle="用于批量问题复核确认与客户整改包准备，问题来自真实任务分析结果。" action={<Button onClick={() => setPage("tasks")}><RefreshCw className="h-4 w-4" />刷新任务</Button>} />
      <Card className="mb-4">
        <div className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr_auto] gap-8 px-7 py-5">
          <SelectBlock label="检查年度" value={year} onChange={onYearChange} options={yearOptions} />
          <SelectBlock label="当前范围" value={scopeValue} onChange={onScopeChange} options={orgOptions} />
          <SelectBlock
            label="问题状态"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: "all", label: "全部状态" },
              { value: "pending", label: "待处理" },
              { value: "confirmed", label: "已确认" },
              { value: "in_package", label: "整改包内" },
              { value: "handled", label: "已处理" },
              { value: "no_issue", label: "无问题" },
            ]}
          />
          <SelectBlock
            label="严重度"
            value={severityFilter}
            onChange={setSeverityFilter}
            options={[
              { value: "all", label: "全部" },
              { value: "high", label: "高风险" },
              { value: "medium", label: "中风险 / 待复核" },
              { value: "low", label: "低风险 / 提示" },
            ]}
          />
          <SelectBlock label="问题类型" value={typeFilter} onChange={setTypeFilter} options={typeOptions} />
          <Button onClick={() => { onYearChange("all"); onScopeChange(ALL_SCOPE_VALUE); setStatusFilter("all"); setSeverityFilter("all"); setTypeFilter("all"); setQuery(""); }}><RefreshCw className="h-4 w-4" />重置</Button>
        </div>
      </Card>
      <div className="mb-4 grid grid-cols-6 gap-4">
        <Metric icon={Building2} label="任务总数" value={`${jobs.length}个`} desc="当前范围真实任务" tone="blue" />
        <Metric icon={Clock} label="待处理" value={`${visibleIssues.filter((i) => !i.workflow || i.workflow.status === "pending").length}个`} desc="当前筛选内未确认" tone="orange" />
        <Metric icon={ShieldCheck} label="高风险" value={`${visibleIssues.filter((i) => isHighRisk(i.severity)).length}个`} desc="当前筛选内优先核查" tone="red" />
        <Metric icon={CheckCircle2} label="已确认" value={`${visibleIssues.filter((i) => i.workflow?.status === "confirmed").length}个`} desc="当前筛选内可打包" tone="green" />
        <Metric icon={PackageCheck} label="整改包内" value={`${visibleIssues.filter((i) => i.workflow?.status === "in_package").length}个`} desc="当前筛选内已纳入" tone="purple" />
        <Metric icon={FileText} label="涉及单位" value={`${new Set(issues.map((i) => jobOrgName(i.job))).size}个`} desc="按真实关联统计" tone="blue" />
      </div>
      <div className="grid grid-cols-[1fr_300px] gap-5">
        <Card title="问题列表" action={<div className="flex h-10 items-center rounded-md border border-slate-200 px-3"><input value={query} onChange={(event) => setQuery(event.target.value)} className="w-52 text-sm outline-none" placeholder="搜索问题/材料" /><Search className="h-4 w-4 text-slate-400" /></div>}>
          <div className="space-y-3 p-4">
            {grouped.length === 0 ? <div className="rounded-md border border-dashed border-slate-200 p-8 text-center text-slate-500">当前范围没有可处理问题。</div> : grouped.map(([orgName, rows], index) => (
              <IssueGroupCard key={orgName} index={index + 1} orgName={orgName} rows={rows} selectedIssue={selectedIssue} selectedIssueKeys={selectedIssueKeys} setSelectedIssueKeys={setSelectedIssueKeys} onSelectProblem={onSelectProblem} setPage={setPage} onWorkflow={onWorkflow} operationBusy={operationBusy} />
            ))}
          </div>
          <PaginationFooter total={visibleIssues.length} label="个问题" />
        </Card>
        <BatchPanel selectedCount={selectedIssueKeys.length} onBatchWorkflow={onBatchWorkflow} setPage={setPage} operationBusy={operationBusy} />
      </div>
    </>
  );
}

function IssueGroupCard({ index, orgName, rows, selectedIssue, selectedIssueKeys, setSelectedIssueKeys, onSelectProblem, setPage, onWorkflow, operationBusy }: { index: number; orgName: string; rows: LiveIssue[]; selectedIssue: LiveIssue | null; selectedIssueKeys: string[]; setSelectedIssueKeys: (keys: string[]) => void; onSelectProblem: (issue: LiveIssue) => void; setPage: (page: PageId) => void; onWorkflow: (issue: LiveIssue, status: IssueWorkflowStatus) => Promise<void>; operationBusy: boolean }) {
  const highRisk = rows.filter((issue) => isHighRisk(issue.severity)).length;
  const pending = rows.filter((issue) => !issue.workflow || issue.workflow.status === "pending").length;
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-sm font-black text-white">{index}</span>
          <b className="text-lg">{orgName}</b>
          <Pill tone="blue">问题总数 {rows.length}</Pill>
          <Pill tone="red">高风险 {highRisk}</Pill>
          <Pill tone="orange">待处理 {pending}</Pill>
        </div>
      </div>
      <table className="w-full table-fixed text-left">
        <thead className="bg-slate-50"><tr><Th>选择</Th><Th>材料</Th><Th>问题编号</Th><Th>问题类型</Th><Th>严重度</Th><Th>页码/证据</Th><Th>状态</Th><Th>操作</Th></tr></thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((issue) => {
            const checked = selectedIssueKeys.includes(issue.key);
            return (
              <tr key={issue.key} className={selectedIssue?.key === issue.key ? "bg-blue-50/50" : undefined}>
                <Td><input type="checkbox" checked={checked} onChange={(event) => setSelectedIssueKeys(event.target.checked ? [...selectedIssueKeys, issue.key] : selectedIssueKeys.filter((key) => key !== issue.key))} /></Td>
                <Td><Pill tone={materialSubjectLabel(issue.job) === "部门" ? "purple" : "blue"}>{materialTypeLabel(issue.job)}</Pill><span className="mt-1 line-clamp-2">{issue.task.filename}</span></Td>
                <Td><b>{issue.ruleId}</b></Td>
                <Td>{issue.category}</Td>
                <Td><Pill tone={severityTone(issue.severity)}>{issue.severityLabel ?? issue.severity}</Pill></Td>
                <Td>第{issue.page || 1}页 <a href={sourceUrl(issue.jobId)} target="_blank" className="ml-2 font-bold text-blue-600">原文</a></Td>
                <Td><Pill tone={workflowTone(issue.workflow?.status)}>{workflowLabel(issue.workflow?.status)}</Pill></Td>
                <Td><div className="flex flex-wrap gap-2 font-bold text-blue-600"><button type="button" onClick={() => { onSelectProblem(issue); setPage("detail"); }}>查看</button><button type="button" onClick={() => void onWorkflow(issue, "confirmed")} disabled={operationBusy}>确认</button><button type="button" onClick={() => void onWorkflow(issue, "no_issue")} disabled={operationBusy}>无问题</button></div></Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BatchPanel({ selectedCount, onBatchWorkflow, setPage, operationBusy }: { selectedCount: number; onBatchWorkflow: (status: IssueWorkflowStatus) => Promise<void>; setPage: (page: PageId) => void; operationBusy: boolean }) {
  return (
    <aside className="space-y-4">
      <Card title="批量处理与整改包" desc="以客户整改输出为目标。">
        <div className="space-y-3 p-5">
          <div className="rounded-md bg-slate-50 p-4"><div className="text-sm text-slate-500">已选择问题</div><div className="mt-2 text-3xl font-black">{selectedCount}<span className="text-lg"> 个</span></div></div>
          <Button variant="primary" className="w-full" onClick={() => void onBatchWorkflow("confirmed")} disabled={operationBusy || selectedCount === 0}>批量确认</Button>
          <Button className="w-full" onClick={() => void onBatchWorkflow("no_issue")} disabled={operationBusy || selectedCount === 0}>批量标记无问题</Button>
          <Button variant="green" className="w-full" onClick={() => setPage("archive")} disabled={selectedCount === 0}>生成整改包</Button>
        </div>
      </Card>
      <Card title="处理说明"><div className="space-y-3 p-5 text-sm text-slate-600"><Explain tone="orange" title="待复核" desc="需要审核员确认问题是否成立" /><Explain tone="green" title="可直接整改" desc="问题明确，可进入整改包" /><Explain tone="purple" title="已加入整改包" desc="进入归档导出候选范围" /></div></Card>
    </aside>
  );
}

function InfoCard({ icon: Icon, title, rows }: { icon: LucideIcon; title: string; rows: [string, string][] }) {
  return (
    <Card>
      <div className="flex h-full p-6">
        <div className="mr-5 flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white"><Icon className="h-6 w-6" /></div>
        <div className="min-w-0 flex-1">
          <h3 className="mb-3 text-lg font-bold">{title}</h3>
          {rows.map(([key, value]) => <div key={key} className="mb-2 grid grid-cols-[100px_1fr] text-sm"><span className="text-slate-500">{key}</span><b className="truncate">{value}</b></div>)}
        </div>
      </div>
    </Card>
  );
}

function ProblemQueue({ issues, selectedIssue, onSelect }: { issues: LiveIssue[]; selectedIssue: LiveIssue | null; onSelect: (issue: LiveIssue) => void }) {
  return (
    <Card title="问题队列">
      <div className="max-h-[650px] space-y-3 overflow-auto p-3">
        {issues.length === 0 ? <div className="p-4 text-sm text-slate-500">暂无问题。</div> : issues.map((issue) => (
          <button key={issue.key} type="button" onClick={() => onSelect(issue)} className={cn("w-full rounded-lg border p-3 text-left", selectedIssue?.key === issue.key ? "border-blue-500 bg-blue-50" : "border-slate-200 bg-white")}>
            <div className="flex justify-between"><b>{issue.ruleId}</b><Pill tone={severityTone(issue.severity)}>{issue.severityLabel ?? issue.severity}</Pill></div>
            <div className="mt-2 line-clamp-2 text-sm font-bold">{issue.title}</div>
            <div className="mt-2 text-xs text-slate-500">位置：第 {issue.page || 1} 页</div>
          </button>
        ))}
      </div>
    </Card>
  );
}

function EvidenceWork({ issue, onWorkflow, setPage, operationBusy }: { issue: LiveIssue | null; onWorkflow: (issue: LiveIssue, status: IssueWorkflowStatus) => Promise<void>; setPage: (page: PageId) => void; operationBusy: boolean }) {
  if (!issue) return <Card title="证据与处理"><div className="p-8 text-center text-slate-500">当前任务暂无可展示问题。</div></Card>;
  return (
    <Card title="证据与处理" desc={`${issue.severityLabel ?? issue.severity} · ${issue.ruleId}`}>
      <div className="space-y-4 p-5">
        <section><h4 className="mb-2 font-black">问题描述</h4><p className="leading-7 text-slate-700">{issue.description || issue.title}</p></section>
        <section><h4 className="mb-2 font-black">证据片段</h4><div className="rounded-md border border-slate-200 bg-slate-50 p-3 leading-6 text-slate-700">{issue.snippet || "当前问题没有文本片段，请打开证据页复核。"}</div></section>
        <section><h4 className="mb-2 font-black">整改建议</h4><textarea value={issue.suggestion} readOnly className="h-24 w-full resize-none rounded-md border border-slate-200 p-3 text-sm leading-6 outline-none" /></section>
        <div className="flex flex-wrap gap-2">
          <Button variant="danger" onClick={() => void onWorkflow(issue, "confirmed")} disabled={operationBusy}>确认问题</Button>
          <Button variant="green" onClick={() => void onWorkflow(issue, "no_issue")} disabled={operationBusy}>标记无问题</Button>
          <Button onClick={() => void onWorkflow(issue, "needs_review")} disabled={operationBusy}>返回人工复核</Button>
          <Button variant="primary" onClick={() => { void onWorkflow(issue, "confirmed"); setPage("archive"); }} disabled={operationBusy}>加入整改包</Button>
        </div>
        <div className="border-t border-slate-100 pt-4 text-sm text-slate-500">当前处理状态：<b>{workflowLabel(issue.workflow?.status)}</b></div>
      </div>
    </Card>
  );
}

function PdfProof({ issue, jobId }: { issue: LiveIssue | null; jobId: string }) {
  return (
    <Card title="原文定位 / 截图证据">
      <div className="p-5">
        <div className="mb-4 flex items-center justify-between text-sm"><span>命中位置：第 {issue?.page || 1} 页</span><a href={sourceUrl(jobId)} target="_blank" className="font-bold text-blue-600">打开原文</a></div>
        <div className="overflow-hidden rounded-md border border-slate-200 bg-slate-50"><img src={previewUrl(jobId)} alt="证据页预览" className="h-[430px] w-full object-contain" /></div>
        <a href={sourceUrl(jobId)} target="_blank" className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md border border-slate-200 bg-white text-sm font-bold text-blue-600"><Download className="h-4 w-4" />打开 / 下载源文件</a>
      </div>
    </Card>
  );
}

function Detail({ selectedOrg, selectedDetail, selectedIssue, setPage, onWorkflow, onReanalyze, onSelectProblem, workflow, operationBusy }: { selectedOrg: OrganizationRecord | null; selectedDetail: SelectedDetail | null; selectedIssue: LiveIssue | null; setPage: (page: PageId) => void; onWorkflow: (issue: LiveIssue, status: IssueWorkflowStatus) => Promise<void>; onReanalyze: (job: JobSummaryRecord) => Promise<void>; onSelectProblem: (issue: LiveIssue) => void; workflow: IssueWorkflowState; operationBusy: boolean }) {
  const detail = selectedDetail;
  const issue = selectedIssue ?? detail?.problem ?? null;
  const task = detail?.task;
  const job = detail?.job;
  const queueIssues = detail && job ? buildIssuesFromDetails([detail.detail], [job], workflow) : [];

  if (!detail || !task || !job) {
    return (
      <>
        <PageTitle selectedOrg={selectedOrg} title="材料详情" subtitle="请先在问题处理台或任务中心选择一个真实任务。" />
        <Card><div className="p-10 text-center text-slate-500">暂无选中的材料。<Button className="ml-4" onClick={() => setPage("issues")}>去选择问题</Button></div></Card>
      </>
    );
  }

  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title={task.filename} subtitle="用于问题证据核对与整改确认，数据来自当前任务详情。" action={<div className="flex gap-2"><Button onClick={() => void onReanalyze(job)} disabled={operationBusy}><RefreshCw className="h-4 w-4" />重新分析</Button><a href={downloadUrl(job.job_id, "pdf")} className="inline-flex h-10 items-center gap-2 rounded-md border border-blue-600 bg-blue-600 px-4 text-sm font-bold text-white"><Download className="h-4 w-4" />导出报告</a></div>} />
      <div className="mb-5 grid grid-cols-3 gap-5">
        <InfoCard icon={FileText} title="材料信息" rows={[["材料名称", task.filename], ["材料类型", materialTypeLabel(job)], ["所属主体", jobOrgName(job)], ["上传时间", formatDateTime(job.created_ts ?? job.ts)], ["版本状态", "当前真实版本"]]} />
        <InfoCard icon={ShieldCheck} title="检查结果" rows={[["问题总数", `${task.problemCount}个`], ["高风险问题", `${task.highRiskCount}个`], ["待处理问题", `${getDisplayIssueTotal(job)}个`], ["整体状态", statusText(job.status)]]} />
        <InfoCard icon={MapPin} title="证据定位" rows={[["命中页数", issue ? `第 ${issue.page || 1} 页` : "未选择问题"], ["命中区域", issue?.bbox ? "已识别 bbox" : "整页预览"], ["最后定位", formatDateTime(job.updated_ts ?? job.ts)], ["原文", "可打开 PDF 证据页"]]} />
      </div>
      <div className="grid grid-cols-[270px_1fr_390px] gap-5">
        <ProblemQueue issues={queueIssues} selectedIssue={issue} onSelect={(problem) => { onSelectProblem(problem); setPage("detail"); }} />
        <EvidenceWork issue={issue} onWorkflow={onWorkflow} setPage={setPage} operationBusy={operationBusy} />
        <PdfProof issue={issue} jobId={job.job_id} />
      </div>
    </>
  );
}

function InfoMini({ icon: Icon, title, value }: { icon: LucideIcon; title: string; value: string }) {
  return <div className="flex items-center gap-3 border-r border-slate-100 last:border-r-0"><Icon className="h-6 w-6 text-slate-700" /><span><div className="text-sm text-slate-500">{title}</div><b>{value}</b></span></div>;
}

function UploadBox({ year, active, uploading, onPick }: { year: string; active: boolean; uploading: boolean; onPick: () => void }) {
  return (
    <Card title={`${year} ${active ? "预算公开材料" : "决算公开材料"}`} action={<Pill tone={active ? "green" : "blue"}>{active ? "预算" : "决算"}</Pill>}>
      <button type="button" onClick={onPick} disabled={uploading} className="m-5 flex h-36 w-[calc(100%-40px)] flex-col items-center justify-center rounded-lg border border-dashed border-blue-300 bg-blue-50/40 text-center disabled:opacity-50">
        {uploading ? <Loader2 className="mb-3 h-9 w-9 animate-spin text-blue-600" /> : <UploadCloud className="mb-3 h-9 w-9 text-blue-600" />}
        <b>{uploading ? "正在上传并启动分析" : "点击选择 PDF 文件上传"}</b>
        <span className="mt-2 text-xs text-slate-500">支持 PDF，上传后进入真实分析任务</span>
      </button>
    </Card>
  );
}

function Match({ label, value, pct, tone }: { label: string; value: string; pct: string; tone: Tone }) {
  const Icon = tone === "green" ? CheckCircle2 : tone === "purple" ? FileArchive : CalendarDays;
  return <div className="flex items-center justify-between rounded-md border border-slate-200 p-4"><div className="flex items-center gap-3"><span className="flex h-9 w-9 items-center justify-center rounded-full bg-blue-50 text-blue-600"><Icon className="h-5 w-5" /></span><span><b>{label}</b><p className="text-xs text-slate-500">{pct}</p></span></div><b>{value}</b></div>;
}

function NextBox({ icon: Icon, title, desc, setPage, target }: { icon: LucideIcon; title: string; desc: string; setPage: (page: PageId) => void; target: PageId }) {
  return <button type="button" onClick={() => setPage(target)} className="flex items-center gap-3 rounded-md border border-slate-200 p-4 text-left hover:bg-slate-50"><Icon className="h-7 w-7 text-blue-600" /><span><b>{title}</b><p className="text-sm text-slate-500">{desc}</p></span><ChevronRight className="ml-auto h-5 w-5 text-slate-400" /></button>;
}

function UploadPage({ selectedOrg, selectedYear, onUploaded, setPage }: { selectedOrg: OrganizationRecord | null; selectedYear: string; onUploaded: () => Promise<void>; setPage: (page: PageId) => void }) {
  const [uploading, setUploading] = useState(false);
  const [docType, setDocType] = useState("dept_budget");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const scopeName = selectedOrgName(selectedOrg);
  const uploadSubject = selectedOrg?.level === "unit" ? "unit" : "dept";
  const uploadSubjectName = selectedOrg?.level === "unit" ? "单位" : "部门";

  const upload = useCallback(async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("fiscal_year", selectedYear);
      formData.append("doc_type", docType);
      if (selectedOrg?.id) formData.append("org_id", selectedOrg.id);
      const uploadResponse = await fetch("/api/documents/upload", { method: "POST", body: formData });
      if (!uploadResponse.ok) throw new Error(await readErrorMessage(uploadResponse));
      const payload = (await uploadResponse.json()) as { job_id?: string };
      if (payload.job_id) {
        const analyzeResponse = await fetch(`/api/analyze/${encodeURIComponent(payload.job_id)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "dual", use_local_rules: true, use_ai_assist: true, fiscal_year: selectedYear, doc_type: docType, org_id: selectedOrg?.id ?? null }),
        });
        if (!analyzeResponse.ok) throw new Error(await readErrorMessage(analyzeResponse));
      }
      await onUploaded();
      setPage("tasks");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }, [docType, onUploaded, selectedOrg?.id, selectedYear, setPage]);

  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title="材料上传与批次提交" subtitle="上传后会创建真实任务并启动分析，任务结果会回到问题处理台和任务中心。" />
      <Card className="mb-5"><div className="grid grid-cols-5 gap-6 p-6"><InfoMini icon={UsersRound} title="工作区" value={scopeName} /><InfoMini icon={CalendarDays} title="检查年度" value={`${selectedYear} 年度`} /><InfoMini icon={Building2} title="单位范围" value={selectedOrg?.level_name ?? selectedOrg?.level ?? "全部"} /><InfoMini icon={UploadCloud} title="上传方式" value="自动识别" /><InfoMini icon={ClipboardCheck} title="检查方式" value="规则 + AI" /></div></Card>
      <div className="grid grid-cols-[1fr_360px] gap-5">
        <div className="grid grid-cols-2 gap-5">{YEAR_OPTIONS.slice(0, 2).map((year, index) => <UploadBox key={year} year={year} active={year === selectedYear} uploading={uploading} onPick={() => { setDocType(`${uploadSubject}_${index === 0 ? "budget" : "final"}`); fileInputRef.current?.click(); }} />)}</div>
        <Card title="自动识别结果"><div className="space-y-3 p-5"><Match label="单位识别匹配" value={scopeName} pct="来自组织库" tone="green" /><Match label="年度识别匹配" value={selectedYear} pct="由表单传入" tone="blue" /><Match label="类型识别匹配" value={`${uploadSubjectName}预算 / ${uploadSubjectName}决算`} pct="按当前层级区分" tone="purple" /></div></Card>
      </div>
      <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" hidden onChange={(event) => void upload(event.target.files)} />
      <Card title="上传后去哪里看" className="mt-5"><div className="grid grid-cols-4 gap-4 p-5"><NextBox icon={FileText} title="任务中心" desc="查看当前任务处理进度" setPage={setPage} target="tasks" /><NextBox icon={ClipboardCheck} title="问题处理" desc="查看已提交材料详情" setPage={setPage} target="issues" /><NextBox icon={Archive} title="导出归档" desc="生成整改包或报告 ZIP" setPage={setPage} target="archive" /><NextBox icon={Settings} title="系统管理" desc="查看组织与单位档案" setPage={setPage} target="settings" /></div></Card>
    </>
  );
}

function Tasks({ selectedOrg, jobs, allJobs, setPage, onSelectJob, onReanalyze, onDeleteJob, operationBusy }: { selectedOrg: OrganizationRecord | null; jobs: JobSummaryRecord[]; allJobs: JobSummaryRecord[]; setPage: (page: PageId) => void; onSelectJob: (job: JobSummaryRecord) => void; onReanalyze: (job: JobSummaryRecord) => Promise<void>; onDeleteJob: (job: JobSummaryRecord) => Promise<void>; operationBusy: boolean }) {
  const rows = jobs.length ? jobs : allJobs.slice(0, 20);
  const running = allJobs.filter((job) => normalizeUiTaskStatus(job.status) === "analyzing").length;
  const completed = allJobs.filter((job) => normalizeUiTaskStatus(job.status) === "completed").length;
  const failed = allJobs.filter((job) => normalizeUiTaskStatus(job.status) === "failed").length;
  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title="任务中心" subtitle="跟踪材料上传、解析、OCR识别、规则审核、AI辅助和导出等任务的执行进度与状态。" />
      <div className="mb-5 grid grid-cols-5 gap-4"><Metric icon={PlayCircle} label="执行中" value={`${running}个`} desc="正在处理" tone="blue" /><Metric icon={CheckCircle2} label="已完成" value={`${completed}个`} desc="完成分析" tone="green" /><Metric icon={AlertTriangle} label="失败" value={`${failed}个`} desc="需要处理" tone="red" /><Metric icon={UploadCloud} label="上传/解析" value={`${allJobs.length}个`} desc="已接入文件" tone="purple" /><Metric icon={Download} label="可导出" value={`${completed}个`} desc="报告可下载" tone="orange" /></div>
      <Card title="任务列表"><table className="w-full table-fixed text-left"><thead className="bg-slate-50"><tr><Th>任务名称</Th><Th>类型</Th><Th>范围</Th><Th>当前步骤</Th><Th>进度</Th><Th>状态</Th><Th>操作</Th></tr></thead><tbody className="divide-y divide-slate-100">{rows.length === 0 ? <tr><Td>暂无任务。</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td></tr> : rows.map((job) => <tr key={job.job_id}><Td><button type="button" onClick={() => { onSelectJob(job); setPage("detail"); }} className="line-clamp-2 text-left font-bold text-blue-600">{toUiTask(job).filename}</button><div className="text-xs text-slate-500">创建时间：{formatDateTime(job.created_ts ?? job.ts)}</div></Td><Td><Pill tone={materialSubjectLabel(job) === "部门" ? "purple" : "blue"}>{materialTypeLabel(job)}</Pill></Td><Td><span title={job.organization_name ?? undefined}>{jobOrgName(job)}</span></Td><Td>{String(job.stage ?? "任务状态")}</Td><Td><div className="h-2 rounded-full bg-slate-100"><div className={cn("h-2 rounded-full", normalizeUiTaskStatus(job.status) === "failed" ? "bg-red-500" : "bg-blue-600")} style={{ width: `${getJobProgress(job)}%` }} /></div><div className="mt-1 text-xs">{getJobProgress(job)}%</div></Td><Td><Pill tone={statusTone(job.status)}>{statusText(job.status)}</Pill></Td><Td><div className="flex flex-wrap gap-2 font-bold text-blue-600"><button type="button" onClick={() => { onSelectJob(job); setPage("detail"); }}>详情</button><button type="button" onClick={() => void onReanalyze(job)} disabled={operationBusy}>重跑</button><a href={downloadUrl(job.job_id, "pdf")}>导出</a><button type="button" onClick={() => void onDeleteJob(job)} disabled={operationBusy} className="text-red-600">删除</button></div></Td></tr>)}</tbody></table><PaginationFooter total={rows.length} /></Card>
    </>
  );
}

function ArchivePage({ selectedOrg, issues, packages, selectedIssueKeys, onCreatePackage, onDownloadPackage, operationBusy }: { selectedOrg: OrganizationRecord | null; issues: LiveIssue[]; packages: RemediationPackageRecord[]; selectedIssueKeys: string[]; onCreatePackage: (keys?: string[]) => Promise<void>; onDownloadPackage: (pkg: RemediationPackageRecord) => Promise<void>; operationBusy: boolean }) {
  const safeIssues = Array.isArray(issues) ? issues : [];
  const safePackages = Array.isArray(packages) ? packages : [];
  const safeSelectedIssueKeys = Array.isArray(selectedIssueKeys) ? selectedIssueKeys : [];
  const confirmed = safeIssues.filter((issue) => issue.workflow?.status === "confirmed");
  const inPackage = safeIssues.filter((issue) => issue.workflow?.status === "in_package");
  return (
    <>
      <PageTitle selectedOrg={selectedOrg} title="导出归档" subtitle="将已确认问题、证据、整改建议和材料版本记录生成客户整改包与年度归档包。" action={<Button variant="primary" onClick={() => void onCreatePackage(safeSelectedIssueKeys.length ? safeSelectedIssueKeys : confirmed.map((issue) => issue.key))} disabled={operationBusy || (safeSelectedIssueKeys.length === 0 && confirmed.length === 0)}>一键生成整改包</Button>} />
      <div className="mb-5 grid grid-cols-5 gap-4"><Metric icon={PackageCheck} label="待生成整改包" value={`${confirmed.length}`} desc="已确认问题" tone="purple" /><Metric icon={Download} label="可下载结果" value={`${safePackages.length}`} desc="单位级结果包" tone="green" /><Metric icon={Archive} label="已归档问题" value={`${inPackage.length}`} desc="整改包内问题" tone="blue" /><Metric icon={CheckCircle2} label="可导出材料" value={`${new Set(safeIssues.map((issue) => issue.jobId)).size}`} desc="涉及任务" tone="orange" /><Metric icon={Clock} label="待处理问题" value={`${safeIssues.filter((issue) => !issue.workflow || issue.workflow.status === "pending").length}`} desc="仍需确认" tone="red" /></div>
      <Card title="整改包列表" desc="按单位输出，便于客户逐项整改。"><table className="w-full text-left"><thead className="bg-slate-50"><tr><Th>整改包名称</Th><Th>单位</Th><Th>问题数</Th><Th>任务数</Th><Th>内容</Th><Th>状态</Th><Th>操作</Th></tr></thead><tbody className="divide-y divide-slate-100">{safePackages.length === 0 ? <tr><Td>暂无整改包，请先确认问题后生成。</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td><Td>-</Td></tr> : safePackages.map((item) => { const issueKeys = normalizeStringArray(item.issue_keys); const jobIds = normalizeStringArray(item.job_ids); const packageItem = { ...item, issue_keys: issueKeys, job_ids: jobIds }; return <tr key={item.id}><Td><b>{item.name}</b><div className="text-xs text-slate-500">生成时间：{formatDateTime(Date.parse(item.created_at))}</div></Td><Td><span title={item.organization_name ?? undefined}>{item.organization_name ? displayOrgName(item.organization_name) : "多单位"}</span></Td><Td>{issueKeys.length}</Td><Td>{jobIds.length}</Td><Td>问题清单 / 证据页 / 处理状态 / 报告链接</Td><Td><Pill tone={item.status === "ready" ? "green" : "orange"}>{item.status}</Pill></Td><Td><Button onClick={() => void onDownloadPackage(packageItem)} disabled={operationBusy}>下载 ZIP</Button></Td></tr>; })}</tbody></table></Card>
    </>
  );
}

function LoadingState() {
  return <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-slate-200 bg-white"><div className="text-center"><Loader2 className="mx-auto h-10 w-10 animate-spin text-blue-600" /><p className="mt-4 text-sm font-bold text-slate-600">正在加载真实系统数据</p></div></div>;
}

function Toasts({ toasts, onClose }: { toasts: Toast[]; onClose: (id: number) => void }) {
  return <div className="fixed right-5 top-20 z-50 space-y-3">{toasts.map((toast) => <div key={toast.id} className="flex min-w-[260px] items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm font-bold shadow-lg"><span className={cn("h-2.5 w-2.5 rounded-full", toast.tone === "red" ? "bg-red-500" : toast.tone === "green" ? "bg-emerald-500" : toast.tone === "orange" ? "bg-orange-500" : "bg-blue-500")} /><span className="flex-1">{toast.text}</span><button type="button" onClick={() => onClose(toast.id)}><X className="h-4 w-4 text-slate-400" /></button></div>)}</div>;
}

export default function GovBudgetCheckerLiveUiDemo() {
  const [page, setPage] = useState<PageId>("workbench");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [orgTree, setOrgTree] = useState<OrganizationRecord[]>([]);
  const [orgList, setOrgList] = useState<OrganizationRecord[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState("");
  const [allJobs, setAllJobs] = useState<JobSummaryRecord[]>([]);
  const [scopedJobs, setScopedJobs] = useState<JobSummaryRecord[]>([]);
  const [details, setDetails] = useState<JobDetailRecord[]>([]);
  const [workflow, setWorkflow] = useState<IssueWorkflowState>(EMPTY_WORKFLOW);
  const [selectedIssueKey, setSelectedIssueKey] = useState("");
  const [selectedIssueKeys, setSelectedIssueKeys] = useState<string[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [operationBusy, setOperationBusy] = useState(false);
  const [refreshSeed, setRefreshSeed] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [selectedYear, setSelectedYear] = useState(CURRENT_YEAR);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedPage = params.get("page");
    if (requestedPage && NAV.some(([id]) => id === requestedPage)) {
      setPage(requestedPage as PageId);
    }
  }, []);

  const pushToast = useCallback((text: string, tone: Tone = "green") => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((current) => [...current, { id, text, tone }].slice(-4));
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4500);
  }, []);

  const selectedOrg = useMemo(() => {
    if (!selectedOrgId) return null;
    return findOrg(orgTree, selectedOrgId) ?? orgList.find((item) => item.id === selectedOrgId) ?? null;
  }, [orgList, orgTree, selectedOrgId]);
  const orgOptions = useMemo<SelectOption[]>(() => {
    const organizations = (orgList.length ? orgList : collectOrgs(orgTree))
      .filter((item) => item.level === "department" || item.level === "unit")
      .sort((left, right) => selectedOrgName(left).localeCompare(selectedOrgName(right), "zh-CN"));
    return [
      { value: ALL_SCOPE_VALUE, label: "全部单位" },
      ...organizations.map((item) => ({
        value: item.id,
        label: selectedOrgName(item),
      })),
    ];
  }, [orgList, orgTree]);
  const scopeValue = selectedOrgId || ALL_SCOPE_VALUE;
  const handleScopeChange = useCallback((value: string) => {
    setSelectedOrgId(value === ALL_SCOPE_VALUE ? "" : value);
    setSelectedIssueKey("");
    setSelectedIssueKeys([]);
  }, []);
  const handleYearChange = useCallback((value: string) => {
    setSelectedYear(value);
    setSelectedIssueKey("");
    setSelectedIssueKeys([]);
  }, []);

  const liveIssues = useMemo(() => buildIssuesFromDetails(details, scopedJobs, workflow), [details, scopedJobs, workflow]);
  const selectedIssue = useMemo(() => liveIssues.find((issue) => issue.key === selectedIssueKey) ?? liveIssues[0] ?? null, [liveIssues, selectedIssueKey]);
  const selectedJob = useMemo(() => selectedJobId ? scopedJobs.find((job) => job.job_id === selectedJobId) ?? allJobs.find((job) => job.job_id === selectedJobId) ?? null : selectedIssue?.job ?? scopedJobs[0] ?? allJobs[0] ?? null, [allJobs, scopedJobs, selectedIssue?.job, selectedJobId]);
  const selectedDetail = useMemo<SelectedDetail | null>(() => {
    if (!selectedJob) return null;
    const detail = details.find((item) => item.job_id === selectedJob.job_id);
    const resolvedDetail: JobDetailRecord = detail ?? { ...selectedJob, job_id: selectedJob.job_id };
    return { job: selectedJob, detail: resolvedDetail, task: toUiTask(resolvedDetail), problem: selectedIssue };
  }, [details, selectedIssue, selectedJob]);

  const loadBase = useCallback(async () => {
    setLoading(true);
    const [userResult, orgResult, orgListResult, jobsResult, workflowResult] = await Promise.all([
      fetchJsonWithAuthState<UserResponse>("/api/auth/me", { user: null }),
      fetchJsonWithAuthState<OrganizationsResponse>("/api/organizations", { tree: [] }),
      fetchJsonWithAuthState<OrganizationsListResponse>("/api/organizations/list", { organizations: [] }),
      fetchJsonWithAuthState<JobsResponse>("/api/jobs?limit=500", []),
      fetchJsonWithAuthState<IssueWorkflowState>("/api/gbc-ui-demo/workflow", EMPTY_WORKFLOW),
    ]);
    if ([userResult, orgResult, orgListResult, jobsResult, workflowResult].some((item) => item.unauthorized)) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    const userPayload = userResult.payload;
    const orgPayload = orgResult.payload;
    const orgListPayload = orgListResult.payload;
    const jobsPayload = jobsResult.payload;
    const workflowPayload = workflowResult.payload;
    const tree = Array.isArray(orgPayload.tree) ? orgPayload.tree : [];
    const list = Array.isArray(orgListPayload.organizations) ? orgListPayload.organizations : collectOrgs(tree);
    const jobs = normalizeJobsPayload(jobsPayload);
    setUser(userPayload.user ?? null);
    setOrgTree(tree);
    setOrgList(list);
    setAllJobs(sortedJobs(jobs));
    setWorkflow(normalizeWorkflowState(workflowPayload));
    setSelectedOrgId((current) => current || "");
    setLoading(false);
  }, []);

  const loadScopedJobs = useCallback(async () => {
    if (!selectedOrgId) {
      setScopedJobs(allJobs);
      return;
    }
    const payload = await fetchJson<OrganizationJobsResponse>(`/api/organizations/${encodeURIComponent(selectedOrgId)}/jobs?include_children=true&limit=200`, { jobs: [] });
    setScopedJobs(sortedJobs(Array.isArray(payload.jobs) ? payload.jobs : []));
  }, [allJobs, selectedOrgId]);

  const loadDetails = useCallback(async () => {
    const jobs = scopedJobs.length ? scopedJobs : allJobs.slice(0, 30);
    const targetJobs = sortedJobs(jobs).slice(0, 30);
    setDetailsLoading(true);
    const nextDetails = await Promise.all(targetJobs.map(async (job) => {
      const [detail, structured] = await Promise.all([
        fetchJson<JobDetailRecord | null>(`/api/jobs/${encodeURIComponent(job.job_id)}`, null),
        fetchJson<StructuredIngestRecord>(`/api/jobs/${encodeURIComponent(job.job_id)}/structured-ingest`, {}),
      ]);
      return detail ? ({ ...detail, structured_ingest: structured } as JobDetailRecord) : null;
    }));
    setDetails(nextDetails.filter((item): item is JobDetailRecord => item !== null));
    setDetailsLoading(false);
  }, [allJobs, scopedJobs]);

  useEffect(() => { void loadBase(); }, [loadBase, refreshSeed]);
  useEffect(() => { void loadScopedJobs(); }, [loadScopedJobs]);
  useEffect(() => { void loadDetails(); }, [loadDetails]);
  useEffect(() => { if (!selectedIssueKey && liveIssues[0]) setSelectedIssueKey(liveIssues[0].key); }, [liveIssues, selectedIssueKey]);

  const refreshAll = useCallback(async () => { setRefreshSeed((value) => value + 1); }, []);
  const logout = useCallback(async () => { try { await fetch("/api/auth/logout", { method: "POST" }); } finally { window.location.href = "/login"; } }, []);

  const updateWorkflowState = useCallback(async (problem: LiveIssue, status: IssueWorkflowStatus) => {
    setOperationBusy(true);
    try {
      const response = await fetch("/api/gbc-ui-demo/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update_issue", job_id: problem.jobId, issue_id: problem.id, status, title: problem.title, severity: problem.severity, page: problem.page, organization_id: problem.job.organization_id, organization_name: problem.task.department, note: problem.suggestion }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const next = (await response.json()) as IssueWorkflowState;
      setWorkflow(normalizeWorkflowState(next));
      pushToast(workflowLabel(status));
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "处理状态保存失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [pushToast]);

  const updateSelectedBatch = useCallback(async (status: IssueWorkflowStatus) => {
    const targets = liveIssues.filter((issue) => selectedIssueKeys.includes(issue.key));
    if (targets.length === 0) { pushToast("请先选择问题", "orange"); return; }
    setOperationBusy(true);
    try {
      for (const issue of targets) {
        const response = await fetch("/api/gbc-ui-demo/workflow", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "update_issue", job_id: issue.jobId, issue_id: issue.id, status, title: issue.title, severity: issue.severity, page: issue.page, organization_id: issue.job.organization_id, organization_name: issue.task.department, note: issue.suggestion }),
        });
        if (!response.ok) throw new Error(await readErrorMessage(response));
      }
      const next = await fetchJson<IssueWorkflowState>("/api/gbc-ui-demo/workflow", EMPTY_WORKFLOW);
      setWorkflow(normalizeWorkflowState(next));
      pushToast(`已批量处理 ${targets.length} 个问题`);
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "批量处理失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [liveIssues, pushToast, selectedIssueKeys]);

  const createPackage = useCallback(async (keys?: string[]) => {
    const issueKeys = keys?.length ? keys : selectedIssueKeys;
    const targets = liveIssues.filter((issue) => issueKeys.includes(issue.key));
    if (targets.length === 0) { pushToast("没有可生成整改包的问题", "orange"); return; }
    setOperationBusy(true);
    try {
      const response = await fetch("/api/gbc-ui-demo/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "create_package", name: `${selectedOrg ? selectedOrgName(selectedOrg) : targets[0]?.task.department ?? "多单位"}整改包`, organization_id: selectedOrg?.id ?? targets[0]?.job.organization_id ?? null, organization_name: selectedOrg ? selectedOrgName(selectedOrg) : targets[0]?.task.department ?? null, job_ids: Array.from(new Set(targets.map((issue) => issue.jobId))), issue_keys: targets.map((issue) => issue.key) }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const payload = (await response.json()) as { state?: IssueWorkflowState };
      setWorkflow(normalizeWorkflowState(payload.state ?? (await fetchJson<IssueWorkflowState>("/api/gbc-ui-demo/workflow", EMPTY_WORKFLOW))));
      setSelectedIssueKeys([]);
      pushToast("整改包已生成");
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "整改包生成失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [liveIssues, pushToast, selectedIssueKeys, selectedOrg]);

  const downloadPackage = useCallback(async (pkg: RemediationPackageRecord) => {
    const jobIds = normalizeStringArray(pkg.job_ids);
    if (jobIds.length === 0) { pushToast("该整改包没有可导出的任务", "orange"); return; }
    setOperationBusy(true);
    try {
      const response = await fetch("/api/reports/download-batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_ids: jobIds }) });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${pkg.name || "reports-batch"}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      pushToast("归档报告包已开始下载");
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "归档报告包下载失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [pushToast]);

  const reanalyzeJob = useCallback(async (job: JobSummaryRecord) => {
    setOperationBusy(true);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/reanalyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "dual", use_local_rules: true, use_ai_assist: true }) });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      pushToast("已启动重新分析");
      await refreshAll();
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "重新分析失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [pushToast, refreshAll]);

  const deleteJob = useCallback(async (job: JobSummaryRecord) => {
    if (!window.confirm(`确定删除任务 ${job.filename ?? job.job_id} 吗？`)) return;
    setOperationBusy(true);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      pushToast("任务已删除");
      await refreshAll();
    } catch (error) {
      pushToast(error instanceof Error ? error.message : "删除任务失败", "red");
    } finally {
      setOperationBusy(false);
    }
  }, [pushToast, refreshAll]);

  const selectProblem = useCallback((problem: LiveIssue) => { setSelectedIssueKey(problem.key); setSelectedJobId(problem.jobId); }, []);
  const selectJob = useCallback((job: JobSummaryRecord) => { setSelectedJobId(job.job_id); const firstIssue = liveIssues.find((issue) => issue.jobId === job.job_id); if (firstIssue) setSelectedIssueKey(firstIssue.key); }, [liveIssues]);

  const body = loading ? <LoadingState /> : page === "workbench" ? (
    <Workbench
      selectedOrg={selectedOrg}
      orgOptions={orgOptions}
      scopeValue={scopeValue}
      onScopeChange={handleScopeChange}
      jobs={scopedJobs}
      issues={liveIssues}
      year={selectedYear}
      onYearChange={handleYearChange}
      setPage={setPage}
      onSelectProblem={selectProblem}
    />
  ) : page === "issues" ? (
    <Issues
      selectedOrg={selectedOrg}
      orgOptions={orgOptions}
      scopeValue={scopeValue}
      onScopeChange={handleScopeChange}
      jobs={scopedJobs}
      issues={liveIssues}
      year={selectedYear}
      onYearChange={handleYearChange}
      selectedIssue={selectedIssue}
      setPage={setPage}
      onSelectProblem={selectProblem}
      onWorkflow={updateWorkflowState}
      onBatchWorkflow={updateSelectedBatch}
      selectedIssueKeys={selectedIssueKeys}
      setSelectedIssueKeys={setSelectedIssueKeys}
      operationBusy={operationBusy}
    />
  ) : page === "upload" ? (
    <UploadPage selectedOrg={selectedOrg} selectedYear={selectedYear === "all" ? CURRENT_YEAR : selectedYear} onUploaded={refreshAll} setPage={setPage} />
  ) : page === "tasks" ? (
    <Tasks selectedOrg={selectedOrg} jobs={scopedJobs} allJobs={allJobs} setPage={setPage} onSelectJob={selectJob} onReanalyze={reanalyzeJob} onDeleteJob={deleteJob} operationBusy={operationBusy} />
  ) : page === "settings" ? (
    <SystemManagementPanel organizations={orgList.length ? orgList : collectOrgs(orgTree)} onRefresh={refreshAll} />
  ) : page === "archive" ? (
    <ArchivePage selectedOrg={selectedOrg} issues={liveIssues} packages={workflow.packages} selectedIssueKeys={selectedIssueKeys} onCreatePackage={createPackage} onDownloadPackage={downloadPackage} operationBusy={operationBusy} />
  ) : (
    <Detail selectedOrg={selectedOrg} selectedDetail={selectedDetail} selectedIssue={selectedIssue} setPage={setPage} onWorkflow={updateWorkflowState} onReanalyze={reanalyzeJob} onSelectProblem={selectProblem} workflow={workflow} operationBusy={operationBusy} />
  );

  return (
    <div className="h-screen overflow-hidden bg-slate-100 text-slate-900" style={{ height: "100vh" }}>
      <Header page={page} setPage={setPage} user={user} onLogout={logout} />
      <div className="flex h-[calc(100vh-70px)] overflow-hidden">
        <Sidebar orgTree={orgTree} selectedOrg={selectedOrg} onSelectOrg={(node) => handleScopeChange(node.id)} />
        <main className="min-w-0 flex-1 overflow-auto overscroll-contain p-7">
          {detailsLoading && !loading ? <div className="mb-4 flex items-center gap-2 rounded-md border border-blue-100 bg-blue-50 px-4 py-2 text-sm font-bold text-blue-700"><Loader2 className="h-4 w-4 animate-spin" />正在同步该范围的问题详情</div> : null}
          {body}
        </main>
      </div>
      <Toasts toasts={toasts} onClose={(id) => setToasts((current) => current.filter((toast) => toast.id !== id))} />
    </div>
  );
}
