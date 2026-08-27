import type { Problem, Task } from "@/lib/mock";
import { getSeverityMeta, normalizeSeverityCode } from "./issueSeverity";

export interface OrganizationRecord {
  id: string;
  name: string;
  level: string;
  level_name?: string;
  parent_id: string | null;
  children?: OrganizationRecord[];
  job_count?: number;
  issue_count?: number;
}

export interface JobSummaryRecord {
  job_id: string;
  filename?: string;
  status?: string;
  progress?: number;
  created_ts?: number;
  updated_ts?: number;
  ts?: number;
  report_year?: number | null;
  report_kind?: "budget" | "final" | "unknown";
  doc_type?: string | null;
  issue_total?: number;
  issue_error?: number;
  issue_warn?: number;
  issue_info?: number;
  merged_issue_total?: number;
  review_item_count?: number;
  organization_id?: string | null;
  organization_name?: string | null;
  organization_level?: string | null;
  organization_match_type?: string | null;
  use_local_rules?: boolean | null;
  use_ai_assist?: boolean | null;
  structured_ingest_status?: string | null;
  structured_tables_count?: number | null;
  structured_recognized_tables?: number | null;
  structured_facts_count?: number | null;
  structured_table_data_count?: number | null;
  structured_line_item_count?: number | null;
  quality_status?: "complete" | "degraded" | "review_required" | string | null;
  analysis_conclusion?: "findings_detected" | "no_findings" | "incomplete" | "analysis_error" | string | null;
  review_reasons?: unknown;
  page_coverage?: number | null;
  scanned_page_count?: number | null;
  stage?: string | null;
  [key: string]: unknown;
}

export interface StructuredIngestRecord {
  status?: string;
  tables_count?: number;
  recognized_tables?: number;
  facts_count?: number;
  review_item_count?: number;
  low_confidence_item_count?: number;
  review_items?: StructuredReviewItem[];
  document_profile?: string;
  document_version_id?: number;
  ps_sync?: {
    report_id?: string;
    table_data_count?: number;
    line_item_count?: number;
    match_mode?: string;
  };
  [key: string]: unknown;
}

export interface StructuredReviewItem {
  id?: string | null;
  type?: string | null;
  severity?: string | null;
  table_code?: string | null;
  page?: number | null;
  page_number?: number | null;
  message?: string | null;
  recommended_action?: string | null;
}

export interface JobDetailRecord extends JobSummaryRecord {
  job_id: string;
  filename?: string;
  doc_type?: string | null;
  result?: Record<string, unknown>;
  structured_ingest?: StructuredIngestRecord;
  ignored_issue_ids?: string[];
  organization_match_confidence?: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function toNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function truncatePreviewText(value: string, maxLength: number): string {
  const text = value.trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}…`;
}

function splitPreviewText(value: string, lineLength: number, maxLines: number): string[] {
  const text = truncatePreviewText(value, lineLength * maxLines);
  if (!text) {
    return [];
  }

  const lines: string[] = [];
  let remaining = text;

  while (remaining && lines.length < maxLines) {
    if (remaining.length <= lineLength || lines.length === maxLines - 1) {
      lines.push(remaining.trim());
      break;
    }

    let breakpoint = remaining.lastIndexOf(" ", lineLength);
    if (breakpoint < Math.floor(lineLength * 0.6)) {
      breakpoint = lineLength;
    }

    lines.push(remaining.slice(0, breakpoint).trim());
    remaining = remaining.slice(breakpoint).trim();
  }

  return lines.filter(Boolean);
}

function renderSvgTextLines(
  lines: string[],
  options: {
    x: number;
    y: number;
    lineHeight: number;
    fontSize: number;
    fill: string;
    fontWeight?: string;
  },
): string {
  return lines
    .map(
      (line, index) => `
        <text
          x="${options.x}"
          y="${options.y + index * options.lineHeight}"
          fill="${options.fill}"
          font-size="${options.fontSize}"
          ${options.fontWeight ? `font-weight="${options.fontWeight}"` : ""}
          font-family="Microsoft YaHei, Arial, sans-serif"
        >${escapeXml(line)}</text>`,
    )
    .join("");
}

export function normalizeTimestamp(value: unknown): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return null;
  }
  return numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
}

export function formatDateTime(value: unknown): string {
  const timestamp = normalizeTimestamp(value);
  if (!timestamp) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(timestamp));
}

export function normalizeUiTaskStatus(value: unknown): Task["status"] {
  const normalized = String(value ?? "").trim().toLowerCase();
  // review_required 必须先判：它是独立终态，既不是 completed 也不是 failed。
  // 归到 completed 会重演"虚假成功"，归到 failed 会误报分析失败。
  if (["review_required", "review", "needs_review"].includes(normalized)) {
    return "review_required";
  }
  // degraded 仍归 completed（结论有效），由 qualityStatus 携带降级标记
  if (["done", "completed", "complete", "success", "succeeded", "degraded"].includes(normalized)) {
    return "completed";
  }
  if (["failed", "error", "failure", "cancelled", "canceled"].includes(normalized)) {
    return "failed";
  }
  return "analyzing";
}

/** 任务是否已跑完（含需复核）。用于区分"还在跑"与"跑完但结论不可交付"。 */
export function isUiTaskFinished(status: Task["status"]): boolean {
  return status === "completed" || status === "review_required" || status === "failed";
}

export function normalizeUiQualityStatus(
  value: unknown,
): NonNullable<Task["qualityStatus"]> | undefined {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "complete" || normalized === "degraded" || normalized === "review_required") {
    return normalized;
  }
  return undefined;
}

export function normalizeAnalysisConclusion(
  value: unknown,
): NonNullable<Task["analysisConclusion"]> | undefined {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (
    normalized === "findings_detected" ||
    normalized === "no_findings" ||
    normalized === "incomplete" ||
    normalized === "analysis_error"
  ) {
    return normalized;
  }
  return undefined;
}

/** 把后端 review_reasons（对象数组）压成可直接展示的中文文案列表。 */
export function extractReviewReasonMessages(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (typeof item === "string") {
        return item.trim();
      }
      if (isRecord(item)) {
        return String(item.message ?? item.code ?? "").trim();
      }
      return "";
    })
    .filter(Boolean);
}

/**
 * 任务状态的展示文案与色调。集中在这里，避免各页面各写一套导致
 * review_required 在某些页面被显示成"失败"。
 */
export function getUiTaskStatusMeta(
  job: Pick<JobSummaryRecord, "status" | "quality_status">,
): { status: Task["status"]; label: string; tone: "green" | "orange" | "red" | "blue" } {
  const status = normalizeUiTaskStatus(job.status);
  if (status === "review_required") {
    return { status, label: "需人工复核", tone: "orange" };
  }
  if (status === "failed") {
    return { status, label: "失败", tone: "red" };
  }
  if (status === "completed") {
    if (normalizeUiQualityStatus(job.quality_status) === "degraded") {
      return { status, label: "完成（部分降级）", tone: "orange" };
    }
    return { status, label: "已完成", tone: "green" };
  }
  return { status, label: "执行中", tone: "blue" };
}

export function getDisplayIssueTotal(
  job: Pick<JobSummaryRecord, "merged_issue_total" | "issue_total" | "review_item_count">,
): number {
  const merged = Number(job.merged_issue_total);
  const ruleAndAiTotal = Number.isFinite(merged) ? merged : toNumber(job.issue_total, 0);
  return ruleAndAiTotal + toNumber(job.review_item_count, 0);
}

export function getHighRiskCount(job: Pick<JobSummaryRecord, "issue_error">): number {
  return toNumber(job.issue_error, 0);
}

function resolvePipelineStepStatus(job: JobSummaryRecord, structured: StructuredIngestRecord) {
  const taskStatus = normalizeUiTaskStatus(job.status);
  const stage = String(job.stage ?? "").trim().toLowerCase();
  const structuredStatus = String(structured.status ?? job.structured_ingest_status ?? "")
    .trim()
    .toLowerCase();
  // review_required 是"跑完了但结论不可交付"，解析/抽取等步骤确实已完成，
  // 不能因为不等于 completed 就把整条流水线显示成未开始。
  const isCompleted = taskStatus === "completed" || taskStatus === "review_required";
  const isRunning = taskStatus === "analyzing";

  const parse: Task["pipeline"]["parse"] =
    isCompleted || isRunning || stage === "parse" ? "done" : "pending";
  const extract: Task["pipeline"]["extract"] =
    structuredStatus === "done"
      ? "done"
      : isRunning || stage === "extract"
        ? "processing"
        : isCompleted
          ? "done"
          : "pending";
  const review: Task["pipeline"]["review"] =
    isCompleted
      ? "done"
      : isRunning || stage === "review"
        ? "processing"
        : "pending";
  const report: Task["pipeline"]["report"] =
    isCompleted
      ? "done"
      : isRunning || stage === "report"
        ? "processing"
        : "pending";

  return { parse, extract, review, report };
}

function getStructuredPayload(job: JobSummaryRecord, structured?: StructuredIngestRecord): StructuredIngestRecord {
  if (structured && Object.keys(structured).length > 0) {
    return structured;
  }
  if (isRecord(job.structured_ingest)) {
    return job.structured_ingest as StructuredIngestRecord;
  }
  return {};
}

function stripFileExtension(filename: string): string {
  return filename.replace(/\.[^.]+$/u, "").trim();
}

function getFallbackSubjectName(filename: string): string {
  const withoutExtension = stripFileExtension(filename);
  const stripped = withoutExtension
    .replace(/[ _-]+/gu, "")
    .replace(/20\d{2}年?/gu, "")
    .replace(/(部门|单位)?(预算|决算)/gu, "")
    .trim();

  return stripped || withoutExtension;
}

function inferReportSubjectType(
  job: Pick<JobSummaryRecord, "organization_level" | "doc_type" | "filename">,
): "department" | "unit" {
  const organizationLevel = String(job.organization_level ?? "").trim().toLowerCase();
  if (organizationLevel === "unit") {
    return "unit";
  }
  if (organizationLevel === "department") {
    return "department";
  }

  const hintText = `${String(job.doc_type ?? "")} ${String(job.filename ?? "")}`.toLowerCase();
  if (hintText.includes("单位") || hintText.includes("unit")) {
    return "unit";
  }
  if (hintText.includes("部门") || hintText.includes("department")) {
    return "department";
  }

  return "department";
}

/**
 * 报告类型标签。unknown 一律显示为"待复核"，绝不猜成"预算"——
 * 猜错类型会让使用者以为系统已按预算口径审过（缺口 P1-05）。
 *
 * 此前这里还有一个 getReportLabel（unknown→"预算"）和一个
 * getDisplayReportLabel（unknown→"类型待确认"），三份并存且口径不一致，
 * 现已合并为这一个唯一实现。
 */
export function resolveReportLabel(
  reportKind: JobSummaryRecord["report_kind"],
  subjectType: "department" | "unit",
): string {
  const subjectLabel = subjectType === "unit" ? "单位" : "部门";
  if (reportKind === "final") {
    return `${subjectLabel}决算`;
  }
  if (reportKind === "budget") {
    return `${subjectLabel}预算`;
  }
  return `${subjectLabel}待复核`;
}

export function formatJobDisplayName(
  job: Pick<
    JobSummaryRecord,
    "job_id" | "filename" | "organization_name" | "organization_level" | "report_year" | "report_kind" | "doc_type"
  >,
): string {
  const rawFilename = String(job.filename ?? "").trim();
  if (job.report_kind === "unknown" && rawFilename) {
    return rawFilename;
  }
  const subjectName =
    String(job.organization_name ?? "").trim() ||
    getFallbackSubjectName(rawFilename) ||
    String(job.job_id ?? "").trim() ||
    "未命名报告";
  const reportYear =
    typeof job.report_year === "number" && job.report_year > 0 ? String(job.report_year) : "";
  const reportLabel = resolveReportLabel(job.report_kind, inferReportSubjectType(job));

  return `${subjectName}${reportYear}${reportLabel}`;
}

export function toUiTask(job: JobSummaryRecord, structuredInput?: StructuredIngestRecord): Task {
  const structured = getStructuredPayload(job, structuredInput);
  const reportYear =
    typeof job.report_year === "number" && job.report_year > 0
      ? String(job.report_year)
      : "--";
  const taskStatus = normalizeUiTaskStatus(job.status);
  const reportLabel = resolveReportLabel(job.report_kind, inferReportSubjectType(job));
  const syncStatus =
    String(structured.status ?? job.structured_ingest_status ?? "")
      .trim()
      .toLowerCase() === "done"
      ? "synced"
      : "pending";

  const task = {
    id: job.job_id,
    filename: String(job.filename ?? job.job_id ?? "未命名文件"),
    department: String(job.organization_name ?? "未关联单位"),
    departmentId: job.organization_id ? String(job.organization_id) : undefined,
    year: reportYear,
    type: job.report_kind === "final" ? "final" : "budget",
    status: taskStatus,
    problemCount: getDisplayIssueTotal(job),
    highRiskCount: getHighRiskCount(job),
    updatedAt: formatDateTime(job.updated_ts ?? job.ts),
    version: 1,
    pipeline: resolvePipelineStepStatus(job, structured),
    structuredData: {
      tables: toNumber(structured.tables_count ?? job.structured_tables_count, 0),
      facts: toNumber(structured.facts_count ?? job.structured_facts_count, 0),
      psTables: toNumber(
        structured.ps_sync?.table_data_count ?? job.structured_table_data_count,
        0,
      ),
      psRows: toNumber(
        structured.ps_sync?.line_item_count ?? job.structured_line_item_count,
        0,
      ),
      syncStatus,
    },
  } as Task;

  task.filename = formatJobDisplayName(job);
  task.department = String(job.organization_name ?? "未关联单位");
  task.reportLabel = reportLabel;
  task.qualityStatus = normalizeUiQualityStatus(job.quality_status);
  task.analysisConclusion = normalizeAnalysisConclusion(job.analysis_conclusion);
  task.reviewReasons = extractReviewReasonMessages(job.review_reasons);

  return task;
}

function inferProblemCategory(issue: Record<string, unknown>): string {
  const ruleId = String(issue.rule_id ?? issue.rule ?? "").trim().toLowerCase();
  const title = `${String(issue.title ?? "")} ${String(issue.message ?? "")}`.toLowerCase();
  const source = String(issue.source ?? "").trim().toLowerCase();
  const location = isRecord(issue.location) ? issue.location : {};
  const normalizedRuleId = ruleId.toUpperCase();
  if (source === "structured_ingest") {
    return "结构化识别复核";
  }
  if (
    ["BUD-109", "V33-227"].includes(normalizedRuleId) ||
    (title.includes("t5") && title.includes("\u540d\u79f0"))
  ) {
    return "\u7c7b\u6b3e\u9879\u53e3\u5f84\u4e00\u81f4\u6027";
  }

  if (source.includes("ai") || ruleId.includes("ai") || title.includes("绩效")) {
    return "AI 智能分析";
  }
  if (location.table_name || title.includes("缺失") || title.includes("九张表") || ruleId.startsWith("bud")) {
    return "表格完整性";
  }
  if (title.includes("勾稽") || title.includes("合计") || title.includes("平衡")) {
    return "表内逻辑关系";
  }
  if (title.includes("口径") || title.includes("一致") || title.includes("文")) {
    return "文数一致性";
  }
  return "基础信息合规";
}

function mapSeverity(value: unknown): Problem["severity"] {
  return normalizeSeverityCode(value);
}

function getPageNumber(issue: Record<string, unknown>): number | undefined {
  const evidence = Array.isArray(issue.evidence) ? issue.evidence.find(isRecord) : null;
  const location = isRecord(issue.location) ? issue.location : null;
  const page = toNumber(evidence?.page ?? (location ? location.page : undefined) ?? issue.page);
  return page > 0 ? page : undefined;
}

export function resolveProblemLocation(issue: Record<string, unknown>, page?: number): string {
  const location = isRecord(issue.location) ? issue.location : {};
  if (location.table_name) {
    return `定位表：${String(location.table_name)}`;
  }
  if (location.table) {
    return `定位表：${String(location.table)}`;
  }
  if (location.section) {
    return `定位章节：${String(location.section)}`;
  }
  return page ? `第 ${page} 页` : "当前无法定位页码";
}

export function createEvidencePreviewDataUrl(
  title: string,
  snippet: string,
  page: number | undefined,
  location: string,
): string {
  const titleLines = splitPreviewText(title, 18, 2);
  const snippetLines = splitPreviewText(snippet || "暂无可用证据", 28, 2);
  const locationLine = truncatePreviewText(location, 34);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="900" height="520" viewBox="0 0 900 520">
      <defs>
        <linearGradient id="bg" x1="0%" x2="100%" y1="0%" y2="100%">
          <stop offset="0%" stop-color="#f8fafc" />
          <stop offset="100%" stop-color="#eef2ff" />
        </linearGradient>
      </defs>
      <rect width="900" height="520" fill="url(#bg)" />
      <rect x="52" y="46" width="796" height="428" rx="24" fill="#ffffff" stroke="#e2e8f0" stroke-width="2" />
      <rect x="702" y="60" width="124" height="42" rx="21" fill="#eef2ff" stroke="#c7d2fe" stroke-width="2" />
      <rect x="96" y="156" width="708" height="168" rx="22" fill="#f8fafc" stroke="#e2e8f0" stroke-width="2" />
      <rect x="130" y="190" width="430" height="18" rx="9" fill="#e2e8f0" />
      <rect x="130" y="224" width="612" height="18" rx="9" fill="#e2e8f0" />
      <rect x="130" y="258" width="560" height="18" rx="9" fill="#e2e8f0" />
      ${renderSvgTextLines(titleLines, { x: 96, y: 98, lineHeight: 38, fontSize: 28, fill: "#0f172a", fontWeight: "700" })}
      ${renderSvgTextLines(snippetLines, { x: 96, y: 340, lineHeight: 34, fontSize: 24, fill: "#475569" })}
      ${renderSvgTextLines([locationLine], { x: 96, y: 408, lineHeight: 28, fontSize: 20, fill: "#64748b" })}
      <text x="764" y="88" text-anchor="middle" fill="#1d4ed8" font-size="20" font-weight="700" font-family="Microsoft YaHei, Arial, sans-serif">${page ? `第 ${page} 页` : "待人工定位"}</text>
    </svg>
  `.trim();

  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function collectLegacyIssues(result: Record<string, unknown>): Record<string, unknown>[] {
  if (isRecord(result.issues)) {
    if (Array.isArray(result.issues.all)) {
      return result.issues.all.filter(isRecord);
    }

    return [
      ...(Array.isArray(result.issues.error) ? result.issues.error.filter(isRecord) : []),
      ...(Array.isArray(result.issues.warn) ? result.issues.warn.filter(isRecord) : []),
      ...(Array.isArray(result.issues.info) ? result.issues.info.filter(isRecord) : []),
    ];
  }

  if (Array.isArray(result.issues)) {
    return result.issues.filter(isRecord);
  }

  return [];
}

function dedupeIssuesById(issues: Record<string, unknown>[]): Record<string, unknown>[] {
  const deduped: Record<string, unknown>[] = [];
  const seen = new Set<string>();

  for (const issue of issues) {
    const issueId = String(issue.id ?? "").trim();
    if (issueId) {
      if (seen.has(issueId)) {
        continue;
      }
      seen.add(issueId);
    }
    deduped.push(issue);
  }

  return deduped;
}

function collectDisplayIssues(result: Record<string, unknown>): Record<string, unknown>[] {
  const aiFindings = Array.isArray(result.ai_findings) ? result.ai_findings.filter(isRecord) : [];
  const ruleFindings = Array.isArray(result.rule_findings) ? result.rule_findings.filter(isRecord) : [];
  const sourceIssues = dedupeIssuesById([...aiFindings, ...ruleFindings]);
  const merged = isRecord(result.merged) ? result.merged : {};
  const mergedIds = Array.isArray(merged.merged_ids)
    ? merged.merged_ids.map((item) => String(item ?? "").trim()).filter(Boolean)
    : [];

  if (mergedIds.length > 0 && sourceIssues.length > 0) {
    const issueById = new Map<string, Record<string, unknown>>();
    for (const issue of sourceIssues) {
      const issueId = String(issue.id ?? "").trim();
      if (issueId && !issueById.has(issueId)) {
        issueById.set(issueId, issue);
      }
    }

    const mergedIssues: Record<string, unknown>[] = [];
    const seen = new Set<string>();
    for (const mergedId of mergedIds) {
      if (seen.has(mergedId)) {
        continue;
      }
      const issue = issueById.get(mergedId);
      if (!issue) {
        continue;
      }
      seen.add(mergedId);
      mergedIssues.push(issue);
    }

    if (mergedIssues.length > 0) {
      return mergedIssues;
    }
  }

  const legacyIssues = collectLegacyIssues(result);
  if (legacyIssues.length > 0) {
    return dedupeIssuesById(legacyIssues);
  }

  if (sourceIssues.length > 0) {
    return sourceIssues;
  }

  return [];
}

function collectStructuredReviewIssues(detail: JobDetailRecord): Record<string, unknown>[] {
  const structured = getStructuredPayload(detail, detail.structured_ingest);
  const reviewItems = Array.isArray(structured.review_items)
    ? structured.review_items.filter(isRecord)
    : [];

  return reviewItems.map((item, index) => {
    const rawPage = toNumber(item.page ?? item.page_number);
    const page = rawPage > 0 ? rawPage : undefined;
    const tableCode = String(item.table_code ?? "").trim();
    const itemType = String(item.type ?? "review").trim() || "review";
    const rawId = String(item.id ?? "").trim();
    const id = rawId || `${detail.job_id}:structured-review:${itemType}:${tableCode || "document"}:${page ?? "unlocated"}:${index + 1}`;
    const title = tableCode
      ? `结构化识别待复核：${tableCode}`
      : "结构化识别结果待复核";
    const message = String(item.message ?? "该项材料识别结果需要人工确认后再作为审校依据。").trim();
    const location: Record<string, unknown> = {};
    if (page) {
      location.page = page;
    }
    if (tableCode) {
      location.table = tableCode;
    }

    return {
      id,
      source: "structured_ingest",
      rule_id: `STRUCTURED-${itemType.toUpperCase()}`,
      severity: String(item.severity ?? "manual_review"),
      title,
      message,
      location,
      evidence: [{ ...(page ? { page } : {}), text: message, text_snippet: message }],
      suggestion: String(item.recommended_action ?? "核对原始 PDF 对应页的表格标题、金额和单位后确认。").trim(),
    };
  });
}

export function toUiProblems(detail: JobDetailRecord, options: { includeEvidencePreview?: boolean } = {}): Problem[] {
  const result = isRecord(detail.result) ? detail.result : {};
  const issues = dedupeIssuesById([
    ...collectDisplayIssues(result),
    ...collectStructuredReviewIssues(detail),
  ]);
  const includeEvidencePreview = options.includeEvidencePreview ?? true;

  const ignored = new Set(
    Array.isArray(detail.ignored_issue_ids)
      ? detail.ignored_issue_ids.map((item) => String(item))
      : [],
  );

  return issues
    .filter((issue) => !ignored.has(String(issue.id ?? "")))
    .map((issue, index) => {
      const page = getPageNumber(issue);
      const evidence = Array.isArray(issue.evidence) ? issue.evidence.find(isRecord) : null;
      const rawLocation = isRecord(issue.location) ? issue.location : {};
      const display = isRecord(issue.display) ? issue.display : {};
      const title = String(issue.title ?? issue.message ?? `问题 ${index + 1}`).trim();
      const resolvedTitle = String(display.summary ?? issue.rule_name ?? title).trim() || title;
      const snippet = String(
        evidence?.text_snippet ?? evidence?.text ?? issue.message ?? title,
      ).trim();
      const location = resolveProblemLocation(issue, page);

      return {
        id: String(issue.id ?? `${detail.job_id}-issue-${index + 1}`),
        ruleId: String(issue.rule_id ?? issue.rule ?? "UNKNOWN"),
        title: resolvedTitle,
        severity: mapSeverity(issue.severity),
        severityLabel: String(issue.severity_label ?? getSeverityMeta(issue.severity).label),
        category: inferProblemCategory(issue),
        page,
        location,
        description: String(issue.message ?? resolvedTitle),
        suggestion: String(
          issue.suggestion ??
            issue.rule_suggestion ??
            display.suggestion ??
            "\u8bf7\u7ed3\u5408\u8868\u683c\u4e0e\u8bf4\u660e\u539f\u6587\u590d\u6838\u540e\u4fee\u6b63"
        ),
        snippet,
        evidenceImage: includeEvidencePreview ? createEvidencePreviewDataUrl(resolvedTitle, snippet, page, location) : "",
        status: "pending",
        source: String(issue.source ?? ""),
        bbox: Array.isArray(issue.bbox)
          ? issue.bbox.map((item) => Number(item))
          : Array.isArray(evidence?.bbox)
            ? evidence.bbox.map((item) => Number(item))
            : undefined,
        expectedName: String(issue.expected_name ?? rawLocation.expected_name ?? ""),
        actualName: String(issue.actual_name ?? rawLocation.actual_name ?? ""),
        codeLevel: String(issue.code_level ?? rawLocation.code_level ?? ""),
        sourceOfTruth: String(issue.source_of_truth ?? rawLocation.source_of_truth ?? ""),
      };
    });
}
