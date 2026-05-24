import "server-only";

import { existsSync } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";

type JsonObject = Record<string, any>;

type StoredOrganization = {
  id: string;
  name: string;
  level: string;
  parent_id: string | null;
  code?: string | null;
  keywords?: string[];
  created_at?: number;
  updated_at?: number;
};

type StoredJobLink = {
  job_id: string;
  org_id: string;
  match_type?: string | null;
  confidence?: number | null;
  created_at?: number;
};

export type LocalOrganizationPayload = {
  id: string;
  name: string;
  level: string;
  level_name: string;
  parent_id: string | null;
  code: string | null;
  keywords: string[];
  created_at: number | null;
  updated_at: number | null;
};

export type LocalOrganizationTreeNode = LocalOrganizationPayload & {
  children: LocalOrganizationTreeNode[];
  job_count: number;
  issue_count: number;
};

export type LocalJobSummary = {
  job_id: string;
  filename: string;
  status: string;
  progress: number;
  ts: number;
  created_ts: number;
  updated_ts: number;
  mode: string;
  dual_mode_enabled: boolean;
  stage: string | null;
  report_year: number | null;
  doc_type: string | null;
  report_kind: "budget" | "final" | "unknown";
  issue_total: number;
  issue_error: number;
  issue_warn: number;
  issue_info: number;
  has_issues: boolean;
  merged_issue_total: number;
  merged_issue_conflicts: number;
  merged_issue_agreements: number;
  top_issue_rules: Array<{ rule_id: string; count: number }>;
  local_participated: boolean;
  ai_participated: boolean;
  local_issue_total: number;
  local_issue_error: number;
  local_issue_warn: number;
  local_issue_info: number;
  ai_issue_total: number;
  ai_issue_error: number;
  ai_issue_warn: number;
  ai_issue_info: number;
  local_elapsed_ms: number;
  ai_elapsed_ms: number;
  provider_stats_count: number;
  structured_ingest_status: string | null;
  structured_document_version_id: number | null;
  structured_tables_count: number | null;
  structured_recognized_tables: number | null;
  structured_facts_count: number | null;
  structured_document_profile: string | null;
  structured_missing_optional_tables: string[];
  review_item_count: number;
  low_confidence_item_count: number;
  structured_report_id: string | null;
  structured_table_data_count: number | null;
  structured_line_item_count: number | null;
  structured_sync_match_mode: string | null;
  organization_id: string | null;
  organization_name: string | null;
  organization_level: string | null;
  organization_match_type: string | null;
  organization_match_confidence: number | null;
};

type DatasetCache = {
  expiresAt: number;
  promise: Promise<LocalDataset> | null;
  value: LocalDataset | null;
};

type OrgStats = {
  job_count: number;
  issue_total: number;
  has_issues: boolean;
};

type LocalDataset = {
  organizations: StoredOrganization[];
  organizationList: LocalOrganizationPayload[];
  departments: Array<LocalOrganizationPayload & { job_count: number; issue_count: number }>;
  tree: LocalOrganizationTreeNode[];
  childrenByParent: Map<string | null, StoredOrganization[]>;
  directStatsByOrgId: Map<string, OrgStats>;
  aggregatedStatsByOrgId: Map<string, OrgStats>;
  jobs: LocalJobSummary[];
  jobDetailsById: Map<string, JsonObject>;
  structuredByJobId: Map<string, JsonObject>;
  orgById: Map<string, StoredOrganization>;
};

export class LocalDataError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function resolveLocalDir(
  envValue: string | undefined,
  candidates: string[],
): string {
  const explicit = envValue?.trim();
  if (explicit) {
    return resolve(explicit);
  }

  return candidates.find((candidate) => existsSync(candidate)) ?? candidates[0];
}

const REPO_ROOT = resolve(process.cwd(), "..");
const DATA_DIR = resolveLocalDir(process.env.LOCAL_DATA_DIR ?? process.env.DATA_DIR, [
  resolve(process.cwd(), "data"),
  resolve(REPO_ROOT, "data"),
]);
const UPLOADS_DIR = resolveLocalDir(process.env.LOCAL_UPLOADS_DIR ?? process.env.UPLOAD_DIR, [
  resolve(process.cwd(), "uploads"),
  resolve(REPO_ROOT, "uploads"),
]);
const ORGANIZATIONS_FILE = resolve(DATA_DIR, "organizations.json");
const JOB_LINKS_FILE = resolve(DATA_DIR, "job_org_links.json");
const CACHE_TTL_MS = 2_000;

const LEVEL_NAMES: Record<string, string> = {
  city: "市",
  district: "区",
  department: "部门",
  unit: "单位",
};

const datasetCache: DatasetCache = {
  expiresAt: 0,
  promise: null,
  value: null,
};

export function invalidateLocalDataCache() {
  datasetCache.expiresAt = 0;
  datasetCache.promise = null;
  datasetCache.value = null;
}

function isRecord(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function toNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function toNullableNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function readPositiveTimestamp(...values: unknown[]): number | null {
  for (const value of values) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric > 1_000_000_000_000 ? numeric / 1000 : numeric;
    }
  }
  return null;
}

function normalizeLookupText(value: unknown): string {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
}

function parseReportYear(...values: unknown[]): number | null {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (!text) {
      continue;
    }

    const direct = Number(text);
    if (Number.isFinite(direct) && direct >= 2000 && direct <= 2099) {
      return direct;
    }

    const match = text.match(/(20\d{2})/);
    if (match) {
      return Number(match[1]);
    }
  }
  return null;
}

function normalizeReportKind(
  reportKind: unknown,
  docType: unknown,
  filename: string,
): "budget" | "final" | "unknown" {
  const explicit = String(reportKind ?? "").trim().toLowerCase();
  if (explicit === "budget" || explicit === "final") {
    return explicit;
  }

  const combined = `${String(docType ?? "")} ${filename}`.toLowerCase();
  if (combined.includes("budget") || combined.includes("预算")) {
    return "budget";
  }
  if (combined.includes("final") || combined.includes("决算") || combined.includes("accounts")) {
    return "final";
  }
  return "unknown";
}

function severityBucket(value: unknown): "error" | "warn" | "info" {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (["critical", "high", "error", "fatal"].includes(normalized)) {
    return "error";
  }
  if (["warn", "warning", "medium", "low", "manual_review"].includes(normalized)) {
    return "warn";
  }
  return "info";
}

function summarizeFindingList(items: unknown): [number, number, number, number] {
  if (!Array.isArray(items)) {
    return [0, 0, 0, 0];
  }

  let errors = 0;
  let warnings = 0;
  let infos = 0;
  for (const item of items) {
    const bucket = severityBucket(isRecord(item) ? item.severity : undefined);
    if (bucket === "error") {
      errors += 1;
    } else if (bucket === "warn") {
      warnings += 1;
    } else {
      infos += 1;
    }
  }

  return [items.length, errors, warnings, infos];
}

function toOrganizationPayload(org: StoredOrganization): LocalOrganizationPayload {
  return {
    id: String(org.id),
    name: String(org.name ?? ""),
    level: String(org.level ?? ""),
    level_name: LEVEL_NAMES[String(org.level ?? "")] ?? String(org.level ?? ""),
    parent_id: org.parent_id ?? null,
    code: org.code ?? null,
    keywords: Array.isArray(org.keywords) ? org.keywords.map((item) => String(item)) : [],
    created_at: toNullableNumber(org.created_at),
    updated_at: toNullableNumber(org.updated_at),
  };
}

async function readJsonFile<T>(path: string, fallback: T): Promise<T> {
  try {
    const raw = await readFile(path, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function buildOrgLookup(orgs: StoredOrganization[]): Map<string, StoredOrganization[]> {
  const lookup = new Map<string, StoredOrganization[]>();

  const push = (key: string, org: StoredOrganization) => {
    if (!key) {
      return;
    }
    const current = lookup.get(key) ?? [];
    current.push(org);
    lookup.set(key, current);
  };

  for (const org of orgs) {
    push(normalizeLookupText(org.name), org);
    for (const keyword of Array.isArray(org.keywords) ? org.keywords : []) {
      push(normalizeLookupText(keyword), org);
    }
  }

  return lookup;
}

function pickMatchedOrganization(
  name: unknown,
  lookup: Map<string, StoredOrganization[]>,
): StoredOrganization | null {
  const key = normalizeLookupText(name);
  if (!key) {
    return null;
  }

  const candidates = lookup.get(key) ?? [];
  if (candidates.length === 0) {
    return null;
  }

  return (
    candidates.find((item) => item.level === "unit") ??
    candidates.find((item) => item.level === "department") ??
    candidates[0]
  );
}

function collectIssueStats(result: JsonObject) {
  let issueTotal = 0;
  let issueError = 0;
  let issueWarn = 0;
  let issueInfo = 0;
  let issueItems: JsonObject[] = [];

  const issues = result.issues;
  if (isRecord(issues)) {
    const errors = Array.isArray(issues.error) ? issues.error.filter(isRecord) : [];
    const warnings = Array.isArray(issues.warn) ? issues.warn.filter(isRecord) : [];
    const infos = Array.isArray(issues.info) ? issues.info.filter(isRecord) : [];
    const all = Array.isArray(issues.all) ? issues.all.filter(isRecord) : [];

    issueError = errors.length;
    issueWarn = warnings.length;
    issueInfo = infos.length;
    issueItems = all.length > 0 ? all : [...errors, ...warnings, ...infos];
    issueTotal = issueItems.length;
  } else if (Array.isArray(issues)) {
    issueItems = issues.filter(isRecord);
    issueTotal = issueItems.length;
    for (const item of issueItems) {
      const bucket = severityBucket(item.severity);
      if (bucket === "error") {
        issueError += 1;
      } else if (bucket === "warn") {
        issueWarn += 1;
      } else {
        issueInfo += 1;
      }
    }
  }

  if (issueItems.length === 0) {
    const fallbackRuleFindings = Array.isArray(result.rule_findings)
      ? result.rule_findings.filter(isRecord)
      : [];
    if (fallbackRuleFindings.length > 0) {
      issueItems = fallbackRuleFindings;
      issueTotal = fallbackRuleFindings.length;
      for (const item of fallbackRuleFindings) {
        const bucket = severityBucket(item.severity);
        if (bucket === "error") {
          issueError += 1;
        } else if (bucket === "warn") {
          issueWarn += 1;
        } else {
          issueInfo += 1;
        }
      }
    }
  }

  const ruleCounter = new Map<string, number>();
  for (const item of issueItems) {
    const ruleId = String(item.rule_id ?? item.rule ?? "").trim();
    if (!ruleId) {
      continue;
    }
    ruleCounter.set(ruleId, (ruleCounter.get(ruleId) ?? 0) + 1);
  }

  const topIssueRules = Array.from(ruleCounter.entries())
    .sort((left, right) => {
      if (right[1] !== left[1]) {
        return right[1] - left[1];
      }
      return left[0].localeCompare(right[0], "en");
    })
    .slice(0, 3)
    .map(([rule_id, count]) => ({ rule_id, count }));

  return {
    issueTotal,
    issueError,
    issueWarn,
    issueInfo,
    topIssueRules,
  };
}

function buildJobSummary(
  jobId: string,
  filename: string,
  detail: JsonObject,
  structured: JsonObject,
  timestamps: {
    createdTs: number;
    updatedTs: number;
    ts: number;
  },
): LocalJobSummary {
  const result = isRecord(detail.result) ? detail.result : {};
  const resultMeta = isRecord(result.meta) ? result.meta : {};
  const issueStats = collectIssueStats(result);
  const mergedTotals = isRecord(result.merged) && isRecord(result.merged.totals) ? result.merged.totals : {};

  let mergedIssueTotal = toNumber(mergedTotals.merged, 0);
  if (mergedIssueTotal <= 0) {
    mergedIssueTotal = issueStats.issueTotal;
  }

  const [localIssueTotal, localIssueError, localIssueWarn, localIssueInfo] = summarizeFindingList(
    result.rule_findings,
  );
  const [aiIssueTotal, aiIssueError, aiIssueWarn, aiIssueInfo] = summarizeFindingList(
    result.ai_findings,
  );

  const elapsedMs = isRecord(resultMeta.elapsed_ms) ? resultMeta.elapsed_ms : {};
  const providerStatsCount = Array.isArray(resultMeta.provider_stats)
    ? resultMeta.provider_stats.length
    : 0;
  const docType = String(detail.doc_type ?? resultMeta.doc_type ?? "").trim() || null;
  const reportYear = parseReportYear(
    detail.report_year,
    detail.fiscal_year,
    resultMeta.report_year,
    resultMeta.fiscal_year,
    filename,
  );
  const reportKind = normalizeReportKind(detail.report_kind, docType, filename);
  const mode = String(detail.mode ?? "legacy").trim() || "legacy";
  const dualModeEnabled =
    Boolean(detail.dual_mode_enabled) ||
    mode === "dual" ||
    Boolean(resultMeta.dual_mode_enabled) ||
    String(resultMeta.mode ?? "").trim().toLowerCase() === "dual";
  const psSync = isRecord(structured.ps_sync) ? structured.ps_sync : {};

  return {
    job_id: jobId,
    filename,
    status: String(detail.status ?? "unknown"),
    progress: toNumber(detail.progress, 0),
    ts: timestamps.ts,
    created_ts: timestamps.createdTs,
    updated_ts: timestamps.updatedTs,
    mode,
    dual_mode_enabled: dualModeEnabled,
    stage: detail.stage ? String(detail.stage) : null,
    report_year: reportYear,
    doc_type: docType,
    report_kind: reportKind,
    issue_total: issueStats.issueTotal,
    issue_error: issueStats.issueError,
    issue_warn: issueStats.issueWarn,
    issue_info: issueStats.issueInfo,
    has_issues: issueStats.issueTotal > 0,
    merged_issue_total: mergedIssueTotal,
    merged_issue_conflicts: toNumber(mergedTotals.conflicts, 0),
    merged_issue_agreements: toNumber(mergedTotals.agreements, 0),
    top_issue_rules: issueStats.topIssueRules,
    local_participated: Boolean(detail.use_local_rules ?? resultMeta.use_local_rules ?? true),
    ai_participated:
      Boolean(detail.use_ai_assist ?? resultMeta.use_ai_assist ?? false) &&
      (dualModeEnabled || aiIssueTotal > 0 || toNumber(elapsedMs.ai, 0) > 0 || providerStatsCount > 0),
    local_issue_total: localIssueTotal > 0 ? localIssueTotal : issueStats.issueTotal,
    local_issue_error: localIssueTotal > 0 ? localIssueError : issueStats.issueError,
    local_issue_warn: localIssueTotal > 0 ? localIssueWarn : issueStats.issueWarn,
    local_issue_info: localIssueTotal > 0 ? localIssueInfo : issueStats.issueInfo,
    ai_issue_total: aiIssueTotal,
    ai_issue_error: aiIssueError,
    ai_issue_warn: aiIssueWarn,
    ai_issue_info: aiIssueInfo,
    local_elapsed_ms: toNumber(elapsedMs.rule, 0),
    ai_elapsed_ms: toNumber(elapsedMs.ai, 0),
    provider_stats_count: providerStatsCount,
    structured_ingest_status: structured.status ? String(structured.status) : null,
    structured_document_version_id: toNullableNumber(structured.document_version_id),
    structured_tables_count: toNullableNumber(structured.tables_count),
    structured_recognized_tables: toNullableNumber(structured.recognized_tables),
    structured_facts_count: toNullableNumber(structured.facts_count),
    structured_document_profile: structured.document_profile
      ? String(structured.document_profile)
      : null,
    structured_missing_optional_tables: Array.isArray(structured.missing_optional_tables)
      ? structured.missing_optional_tables.map((item) => String(item))
      : [],
    review_item_count: toNumber(structured.review_item_count, 0),
    low_confidence_item_count: toNumber(structured.low_confidence_item_count, 0),
    structured_report_id: psSync.report_id ? String(psSync.report_id) : null,
    structured_table_data_count: toNullableNumber(psSync.table_data_count),
    structured_line_item_count: toNullableNumber(psSync.line_item_count),
    structured_sync_match_mode: psSync.match_mode ? String(psSync.match_mode) : null,
    organization_id: detail.organization_id ? String(detail.organization_id) : null,
    organization_name: detail.organization_name ? String(detail.organization_name) : null,
    organization_level: detail.organization_level ? String(detail.organization_level) : null,
    organization_match_type: detail.organization_match_type
      ? String(detail.organization_match_type)
      : null,
    organization_match_confidence: toNullableNumber(detail.organization_match_confidence),
  };
}

async function buildDataset(): Promise<LocalDataset> {
  const organizationsPayload = await readJsonFile<{ organizations?: StoredOrganization[] }>(
    ORGANIZATIONS_FILE,
    { organizations: [] },
  );
  const linksPayload = await readJsonFile<{ links?: StoredJobLink[] }>(JOB_LINKS_FILE, { links: [] });

  const organizations = Array.isArray(organizationsPayload.organizations)
    ? organizationsPayload.organizations.filter(isRecord).map((item) => ({
        id: String(item.id ?? ""),
        name: String(item.name ?? ""),
        level: String(item.level ?? ""),
        parent_id: item.parent_id ? String(item.parent_id) : null,
        code: item.code ? String(item.code) : null,
        keywords: Array.isArray(item.keywords) ? item.keywords.map((value) => String(value)) : [],
        created_at: toNullableNumber(item.created_at) ?? undefined,
        updated_at: toNullableNumber(item.updated_at) ?? undefined,
      }))
    : [];
  const links = Array.isArray(linksPayload.links)
    ? linksPayload.links.filter(isRecord).map((item) => ({
        job_id: String(item.job_id ?? ""),
        org_id: String(item.org_id ?? ""),
        match_type: item.match_type ? String(item.match_type) : null,
        confidence: toNullableNumber(item.confidence),
        created_at: toNullableNumber(item.created_at) ?? undefined,
      }))
    : [];

  const orgById = new Map<string, StoredOrganization>();
  const childrenByParent = new Map<string | null, StoredOrganization[]>();
  for (const org of organizations) {
    orgById.set(org.id, org);
    const siblings = childrenByParent.get(org.parent_id ?? null) ?? [];
    siblings.push(org);
    childrenByParent.set(org.parent_id ?? null, siblings);
  }

  const orgLookup = buildOrgLookup(organizations);
  const linkByJobId = new Map<string, StoredJobLink>();
  for (const link of links) {
    if (link.job_id) {
      linkByJobId.set(link.job_id, link);
    }
  }

  const jobDetailsById = new Map<string, JsonObject>();
  const structuredByJobId = new Map<string, JsonObject>();
  const jobs: LocalJobSummary[] = [];

  let uploadEntries: Awaited<ReturnType<typeof readdir>> = [];
  try {
    uploadEntries = await readdir(UPLOADS_DIR, { withFileTypes: true });
  } catch {
    uploadEntries = [];
  }

  for (const entry of uploadEntries) {
    if (!entry.isDirectory()) {
      continue;
    }

    const jobDir = resolve(UPLOADS_DIR, entry.name);
    let fileEntries: Awaited<ReturnType<typeof readdir>> = [];
    try {
      fileEntries = await readdir(jobDir, { withFileTypes: true });
    } catch {
      continue;
    }

    const hasStatusFile = fileEntries.some((item) => item.isFile() && item.name === "status.json");
    const pdfEntry = fileEntries.find(
      (item) => item.isFile() && item.name.toLowerCase().endsWith(".pdf"),
    );
    if (!hasStatusFile && !pdfEntry) {
      continue;
    }

    const statusPath = resolve(jobDir, "status.json");
    const structuredPath = resolve(jobDir, "structured_ingest.json");
    const rawDetail = await readJsonFile<JsonObject>(statusPath, {});
    const rawStructured = await readJsonFile<JsonObject>(structuredPath, {});
    const structured =
      Object.keys(rawStructured).length > 0
        ? rawStructured
        : isRecord(rawDetail.structured_ingest)
          ? rawDetail.structured_ingest
          : {};

    const detail: JsonObject = {
      ...rawDetail,
      job_id: entry.name,
    };

    const filename = String(
      detail.filename ??
        rawDetail.filename ??
        pdfEntry?.name ??
        "",
    ).trim();
    if (filename) {
      detail.filename = filename;
    }
    if (Object.keys(structured).length > 0) {
      detail.structured_ingest = structured;
    }

    let matchedOrg: StoredOrganization | null = null;
    let matchType: string | null = null;
    let confidence: number | null = null;

    const linked = linkByJobId.get(entry.name);
    if (linked) {
      matchedOrg = orgById.get(linked.org_id) ?? null;
      matchType = linked.match_type ?? null;
      confidence = linked.confidence ?? null;
    }

    if (!matchedOrg && detail.organization_id) {
      matchedOrg = orgById.get(String(detail.organization_id)) ?? null;
      matchType = String(detail.organization_match_type ?? "").trim() || null;
      confidence = toNullableNumber(detail.organization_match_confidence);
    }

    if (!matchedOrg) {
      matchedOrg =
        pickMatchedOrganization(detail.organization_name, orgLookup) ??
        pickMatchedOrganization(structured.organization_name, orgLookup);
      if (matchedOrg) {
        matchType = matchType ?? (String(detail.organization_match_type ?? "auto").trim() || "auto");
        confidence = confidence ?? toNullableNumber(detail.organization_match_confidence) ?? 1;
      }
    }

    if (matchedOrg) {
      detail.organization_id = matchedOrg.id;
      detail.organization_name = matchedOrg.name;
      detail.organization_level = matchedOrg.level;
      detail.organization_match_type = matchType ?? detail.organization_match_type ?? "auto";
      detail.organization_match_confidence =
        confidence ?? detail.organization_match_confidence ?? 1;
    } else {
      detail.organization_id = detail.organization_id ? String(detail.organization_id) : null;
      detail.organization_name = detail.organization_name ? String(detail.organization_name) : null;
      detail.organization_level = detail.organization_level
        ? String(detail.organization_level)
        : null;
      detail.organization_match_type = detail.organization_match_type
        ? String(detail.organization_match_type)
        : null;
      detail.organization_match_confidence = toNullableNumber(
        detail.organization_match_confidence,
      );
    }

    const dirStat = await stat(jobDir).catch(() => null);
    const statusStat = await stat(statusPath).catch(() => null);

    const createdTs =
      readPositiveTimestamp(
        detail.job_created_at,
        detail.version_created_at,
        statusStat?.ctimeMs,
        statusStat?.mtimeMs,
        dirStat?.ctimeMs,
        dirStat?.mtimeMs,
      ) ?? Date.now() / 1000;
    const updatedTs =
      readPositiveTimestamp(
        statusStat?.mtimeMs,
        dirStat?.mtimeMs,
        detail.ts,
      ) ?? createdTs;
    const ts = readPositiveTimestamp(detail.ts, updatedTs, createdTs) ?? updatedTs;

    const summary = buildJobSummary(entry.name, filename, detail, structured, {
      createdTs,
      updatedTs,
      ts,
    });

    jobDetailsById.set(entry.name, detail);
    structuredByJobId.set(entry.name, structured);
    jobs.push(summary);
  }

  jobs.sort((left, right) => right.ts - left.ts);

  const directStatsByOrgId = new Map<string, OrgStats>();
  for (const org of organizations) {
    directStatsByOrgId.set(org.id, { job_count: 0, issue_total: 0, has_issues: false });
  }

  for (const job of jobs) {
    if (!job.organization_id) {
      continue;
    }
    const current = directStatsByOrgId.get(job.organization_id) ?? {
      job_count: 0,
      issue_total: 0,
      has_issues: false,
    };
    current.job_count += 1;
    current.issue_total += job.merged_issue_total || job.issue_total;
    current.has_issues = current.issue_total > 0;
    directStatsByOrgId.set(job.organization_id, current);
  }

  const aggregatedStatsByOrgId = new Map<string, OrgStats>();
  const walkStats = (orgId: string): OrgStats => {
    const direct = directStatsByOrgId.get(orgId) ?? {
      job_count: 0,
      issue_total: 0,
      has_issues: false,
    };
    const next: OrgStats = {
      job_count: direct.job_count,
      issue_total: direct.issue_total,
      has_issues: direct.has_issues,
    };
    for (const child of childrenByParent.get(orgId) ?? []) {
      const childStats = walkStats(child.id);
      next.job_count += childStats.job_count;
      next.issue_total += childStats.issue_total;
    }
    next.has_issues = next.issue_total > 0;
    aggregatedStatsByOrgId.set(orgId, next);
    return next;
  };

  for (const org of organizations) {
    if (!aggregatedStatsByOrgId.has(org.id)) {
      walkStats(org.id);
    }
  }

  const buildTreeNode = (org: StoredOrganization): LocalOrganizationTreeNode => {
    const payload = toOrganizationPayload(org);
    const stats = aggregatedStatsByOrgId.get(org.id) ?? {
      job_count: 0,
      issue_total: 0,
      has_issues: false,
    };
    return {
      ...payload,
      children: (childrenByParent.get(org.id) ?? [])
        .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"))
        .map(buildTreeNode),
      job_count: stats.job_count,
      issue_count: stats.issue_total,
    };
  };

  const organizationList = organizations
    .map((org) => toOrganizationPayload(org))
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
  const tree = (childrenByParent.get(null) ?? [])
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"))
    .map(buildTreeNode);
  const departments = organizations
    .filter((org) => org.level === "department")
    .map((org) => {
      const payload = toOrganizationPayload(org);
      const stats = aggregatedStatsByOrgId.get(org.id) ?? {
        job_count: 0,
        issue_total: 0,
        has_issues: false,
      };
      return {
        ...payload,
        job_count: stats.job_count,
        issue_count: stats.issue_total,
      };
    })
    .sort((left, right) => right.issue_count - left.issue_count || left.name.localeCompare(right.name, "zh-CN"));

  return {
    organizations,
    organizationList,
    departments,
    tree,
    childrenByParent,
    directStatsByOrgId,
    aggregatedStatsByOrgId,
    jobs,
    jobDetailsById,
    structuredByJobId,
    orgById,
  };
}

async function getDataset(): Promise<LocalDataset> {
  const now = Date.now();
  if (datasetCache.value && now < datasetCache.expiresAt) {
    return datasetCache.value;
  }
  if (datasetCache.promise) {
    return datasetCache.promise;
  }

  datasetCache.promise = buildDataset()
    .then((dataset) => {
      datasetCache.value = dataset;
      datasetCache.expiresAt = Date.now() + CACHE_TTL_MS;
      datasetCache.promise = null;
      return dataset;
    })
    .catch((error) => {
      datasetCache.promise = null;
      throw error;
    });

  return datasetCache.promise;
}

function ensureOrganization(dataset: LocalDataset, orgId: string): StoredOrganization {
  const org = dataset.orgById.get(orgId);
  if (!org) {
    throw new LocalDataError(404, "organization not found");
  }
  return org;
}

function collectDescendantOrgIds(dataset: LocalDataset, orgId: string): string[] {
  const collected: string[] = [];
  const stack = [orgId];
  const seen = new Set<string>();
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || seen.has(current)) {
      continue;
    }
    seen.add(current);
    collected.push(current);
    for (const child of dataset.childrenByParent.get(current) ?? []) {
      stack.push(child.id);
    }
  }
  return collected;
}

export async function getLocalOrganizationsTree() {
  const dataset = await getDataset();
  return {
    tree: dataset.tree,
    total: dataset.organizations.length,
  };
}

export async function getLocalOrganizationsList() {
  const dataset = await getDataset();
  return {
    organizations: dataset.organizationList,
    total: dataset.organizationList.length,
  };
}

export async function getLocalDepartments() {
  const dataset = await getDataset();
  return {
    departments: dataset.departments,
    total: dataset.departments.length,
  };
}

export async function getLocalDepartmentUnits(deptId: string) {
  const dataset = await getDataset();
  const department = ensureOrganization(dataset, deptId);
  if (department.level !== "department") {
    throw new LocalDataError(404, "department not found");
  }

  const units = (dataset.childrenByParent.get(deptId) ?? [])
    .filter((org) => org.level === "unit")
    .map((org) => toOrganizationPayload(org))
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));

  return {
    units,
    total: units.length,
  };
}

export async function getLocalDepartmentStats(deptId: string) {
  const dataset = await getDataset();
  const department = ensureOrganization(dataset, deptId);
  if (department.level !== "department") {
    throw new LocalDataError(404, "department not found");
  }

  const stats: Record<string, OrgStats> = {};
  stats[deptId] = dataset.directStatsByOrgId.get(deptId) ?? {
    job_count: 0,
    issue_total: 0,
    has_issues: false,
  };
  for (const unit of dataset.childrenByParent.get(deptId) ?? []) {
    if (unit.level !== "unit") {
      continue;
    }
    stats[unit.id] = dataset.directStatsByOrgId.get(unit.id) ?? {
      job_count: 0,
      issue_total: 0,
      has_issues: false,
    };
  }

  return {
    department_id: deptId,
    stats,
  };
}

export async function getLocalJobs(options?: {
  limit?: number | null;
  offset?: number;
}) {
  const dataset = await getDataset();
  const limit = typeof options?.limit === "number" ? options.limit : null;
  const offset = Math.max(0, Number(options?.offset ?? 0));
  if (limit === null && offset === 0) {
    return dataset.jobs;
  }

  const items = limit === null ? dataset.jobs.slice(offset) : dataset.jobs.slice(offset, offset + limit);
  return {
    items,
    total: dataset.jobs.length,
    limit,
    offset,
  };
}

export async function getLocalOrganizationJobs(
  orgId: string,
  options?: {
    include_children?: boolean;
    limit?: number | null;
    offset?: number;
  },
) {
  const dataset = await getDataset();
  ensureOrganization(dataset, orgId);

  const includeChildren = Boolean(options?.include_children);
  const scope = new Set(includeChildren ? collectDescendantOrgIds(dataset, orgId) : [orgId]);
  const matchingJobs = dataset.jobs.filter(
    (job) => Boolean(job.organization_id) && scope.has(String(job.organization_id)),
  );

  const limit = typeof options?.limit === "number" ? options.limit : null;
  const offset = Math.max(0, Number(options?.offset ?? 0));
  const jobs = limit === null ? matchingJobs.slice(offset) : matchingJobs.slice(offset, offset + limit);

  return {
    jobs,
    total: matchingJobs.length,
    limit,
    offset,
  };
}

export async function getLocalJobDetail(jobId: string) {
  const dataset = await getDataset();
  const detail = dataset.jobDetailsById.get(jobId);
  if (!detail) {
    throw new LocalDataError(404, "job_id does not exist");
  }
  return detail;
}

export async function getLocalStructuredIngest(jobId: string) {
  const dataset = await getDataset();
  if (!dataset.jobDetailsById.has(jobId)) {
    throw new LocalDataError(404, "job_id does not exist");
  }

  const structured = dataset.structuredByJobId.get(jobId);
  if (structured && Object.keys(structured).length > 0) {
    return structured;
  }

  return {
    job_id: jobId,
    status: "pending",
    review_item_count: 0,
    review_items: [],
  };
}
