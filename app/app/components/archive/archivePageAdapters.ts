/**
 * 导出归档页（Task 9）的纯逻辑层：整改包生成与下载能力的迁移适配。
 *
 * 迁移来源：旧单体 app/app/viewer/gbc-ui-demo/page.tsx 的 ArchivePage +
 * createPackage + downloadPackage（该单体 Task 10 才删除，本批只迁移不删）。
 * 后端接口全部沿用既有：
 * - GET/POST /api/workflow（问题工作流唯一路径；create_package 动作把打包
 *   问题置为 in_package 并落一条 package 记录，见
 *   src/services/issue_workflow_store.py）；
 * - POST /api/reports/download-batch（按 job_ids 批量导出 ZIP）。
 *
 * 拆到无 JSX 依赖的 .ts 文件的理由与 workbenchAdapters.ts 一致：
 * 这里的判定（打包目标怎么选、payload 怎么拼、反例"无已确认问题不得生成
 * 空整改包"）必须能被 jiti 单测直接断言。
 */

import type {
  IssueWorkflowRecord,
  IssueWorkflowState,
  RemediationPackageRecord,
} from "../../../lib/issueWorkflowTypes";

// ---------------------------------------------------------------------------
// 工作流状态的防御性归一（沿用旧单体 normalizeWorkflowState 的容错语义）
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

const VALID_ISSUE_STATUSES = new Set(["pending", "confirmed", "no_issue", "needs_review", "in_package"]);
const VALID_PACKAGE_STATUSES = new Set(["draft", "ready", "submitted"]);

/** 把 /api/workflow 的响应归一为强类型状态；坏数据跳过而不是让整页崩掉。 */
export function normalizeWorkflowState(value: unknown): IssueWorkflowState {
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
    const status = VALID_ISSUE_STATUSES.has(statusText) ? (statusText as IssueWorkflowRecord["status"]) : "pending";
    const resolvedKey = String(item.key ?? key ?? `${jobId}::${issueId}`).trim();
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

  const packages: RemediationPackageRecord[] = Array.isArray(raw.packages)
    ? raw.packages
        .filter(isRecord)
        .map((item, index) => {
          const statusText = String(item.status ?? "").trim();
          const status = VALID_PACKAGE_STATUSES.has(statusText)
            ? (statusText as RemediationPackageRecord["status"])
            : "draft";
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

// ---------------------------------------------------------------------------
// KPI 汇总（全部来自工作流状态的真实计数，不渲染未经度量的数字）
// ---------------------------------------------------------------------------

export interface ArchiveSummary {
  /** 待生成整改包 = 已确认（confirmed）问题数——旧页同口径。 */
  confirmedCount: number;
  /** 可下载结果 = 整改包条数。 */
  packageCount: number;
  /** 已归档问题 = in_package 问题数。 */
  inPackageCount: number;
}

export function deriveArchiveSummary(state: IssueWorkflowState | null | undefined): ArchiveSummary | null {
  if (!state) {
    // null 语义：工作流状态尚未加载——渲染层据此显示加载态而不是 0。
    return null;
  }
  const records = Object.values(state.issues);
  return {
    confirmedCount: records.filter((record) => record.status === "confirmed").length,
    packageCount: Array.isArray(state.packages) ? state.packages.length : 0,
    inPackageCount: records.filter((record) => record.status === "in_package").length,
  };
}

/** 已确认问题清单（打包目标候选），按更新时间倒序——最新确认的排前面。 */
export function selectConfirmedIssues(state: IssueWorkflowState | null | undefined): IssueWorkflowRecord[] {
  if (!state) {
    return [];
  }
  return Object.values(state.issues)
    .filter((record) => record.status === "confirmed")
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

// ---------------------------------------------------------------------------
// create_package 请求体（与旧单体 createPackage 的拼装口径一致）
// ---------------------------------------------------------------------------

export interface CreatePackagePayload {
  action: "create_package";
  name: string;
  organization_id: string | null;
  organization_name: string | null;
  job_ids: string[];
  issue_keys: string[];
}

/**
 * 由打包目标（已确认问题记录）拼装 create_package 请求体。
 *
 * 与旧单体等价：
 * - issue_keys = 目标问题的 workflow key（job_id::issue_id）；
 * - job_ids = 目标问题涉及的任务去重（旧单体 Array.from(new Set(...))）；
 * - 组织归属：全部目标同一组织时取该组织，跨组织时为 null（旧单体取
 *   selectedOrg 或首个目标的组织；新页无全局组织筛选，统一按"同一组织才落名"
 *   的口径，名称相应为「{组织名}整改包」或「多单位整改包」）。
 *
 * 反例（核心断言）：目标为空时返回 null——调用方必须据此拒绝生成，
 * 不得发出空 issue_keys 的请求（后端也会 400，但前端先拦住并给出提示）。
 */
export function buildCreatePackagePayload(targets: IssueWorkflowRecord[]): CreatePackagePayload | null {
  if (targets.length === 0) {
    return null;
  }
  const jobIds = Array.from(new Set(targets.map((record) => record.job_id)));
  const organizationIds = new Set(
    targets.map((record) => String(record.organization_id ?? "").trim()).filter(Boolean),
  );
  const organizationNames = new Set(
    targets.map((record) => String(record.organization_name ?? "").trim()).filter(Boolean),
  );
  const singleOrganization = organizationIds.size === 1;
  const organizationId = singleOrganization ? Array.from(organizationIds)[0] : null;
  const organizationName = singleOrganization ? Array.from(organizationNames)[0] ?? null : null;
  return {
    action: "create_package",
    name: `${organizationName ?? "多单位"}整改包`,
    organization_id: organizationId,
    organization_name: organizationName,
    job_ids: jobIds,
    issue_keys: targets.map((record) => record.key),
  };
}

// ---------------------------------------------------------------------------
// 整改包列表展示口径（沿用旧页的列语义）
// ---------------------------------------------------------------------------

/** 整改包列表「内容」列的固定文案（与旧单体一致，描述 ZIP 内含物）。 */
export const PACKAGE_CONTENT_TEXT = "问题清单 / 证据页 / 处理状态 / 报告链接";

export function resolvePackageStatusLabel(status: RemediationPackageRecord["status"]): string {
  if (status === "ready") {
    return "ready · 可下载";
  }
  if (status === "submitted") {
    return "submitted · 已提交";
  }
  return "draft · 草稿";
}

export function resolvePackageStatusTone(
  status: RemediationPackageRecord["status"],
): "done" | "review" | "neutral" {
  if (status === "ready") {
    return "done";
  }
  if (status === "submitted") {
    return "neutral";
  }
  return "review";
}

/** 下载文件名：与旧单体一致用「{整改包名}.zip」，名为空时退回 reports-batch。 */
export function buildPackageDownloadFilename(pkg: RemediationPackageRecord): string {
  return `${pkg.name || "reports-batch"}.zip`;
}

/** 整改包的下载请求体：旧单体按 job_ids 调 /api/reports/download-batch。 */
export function buildPackageDownloadBody(pkg: RemediationPackageRecord): { job_ids: string[] } {
  return { job_ids: normalizeStringArray(pkg.job_ids) };
}
