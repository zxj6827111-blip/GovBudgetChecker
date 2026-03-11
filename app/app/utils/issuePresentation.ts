import type { IssueItem } from "../components/IssueTabs";

export type IssuePresentation = {
  summary: string;
  pageText: string;
  locationText: string;
  evidenceText: string;
  detailLines: string[];
};

type DisplayPayload = {
  summary?: string;
  page_text?: string;
  pageText?: string;
  location_text?: string;
  locationText?: string;
  evidence_text?: string;
  evidenceText?: string;
  detail_lines?: string[];
  detailLines?: string[];
};

export function getIssuePresentation(issue: IssueItem): IssuePresentation {
  const display = (issue.display || {}) as DisplayPayload;
  const summary =
    cleanText(display.summary) ||
    cleanText(issue.title) ||
    firstSentence(issue.message) ||
    "未命名问题";

  const pageText =
    cleanText(display.page_text) ||
    cleanText(display.pageText) ||
    buildPageText(issue);

  const locationText =
    cleanText(display.location_text) ||
    cleanText(display.locationText) ||
    buildLocationText(issue, pageText);

  const evidenceText =
    cleanText(display.evidence_text) ||
    cleanText(display.evidenceText) ||
    getPrimaryEvidenceText(issue);

  const detailLines = dedupeLines([
    ...normalizeLines(display.detail_lines),
    ...normalizeLines(display.detailLines),
    ...buildFallbackDetails(issue, evidenceText),
  ]);

  return {
    summary,
    pageText,
    locationText,
    evidenceText,
    detailLines,
  };
}

function buildFallbackDetails(issue: IssueItem, evidenceText: string): string[] {
  const lines: string[] = [];
  const message = cleanText(issue.message);
  const title = cleanText(issue.title);
  if (message && message !== title) {
    lines.push(message);
  }

  const suggestion = cleanText(issue.suggestion);
  if (suggestion) {
    lines.push(`建议：${suggestion}`);
  }

  const whyNot = cleanText(issue.why_not);
  if (
    whyNot &&
    !whyNot.startsWith("NO_") &&
    !whyNot.startsWith("AI_LOCATED:") &&
    !whyNot.startsWith("TOLERANCE_FILTERED:")
  ) {
    lines.push(`说明：${whyNot}`);
  }

  const metricLines = buildMetricLines(issue.metrics);
  lines.push(...metricLines);

  if (evidenceText) {
    lines.push(`证据：${evidenceText}`);
  }

  return lines;
}

function buildMetricLines(metrics: Record<string, any> | undefined): string[] {
  if (!metrics || typeof metrics !== "object") return [];

  return Object.entries(metrics)
    .slice(0, 6)
    .map(([key, value]) => {
      const label = humanizeKey(key);
      const text = formatMetricValue(value);
      if (!label || !text) return "";
      return `${label}：${text}`;
    })
    .filter(Boolean);
}

function buildPageText(issue: IssueItem): string {
  const pages = collectPages(issue);
  if (pages.length === 0) return "";
  if (pages.length === 1) return `第 ${pages[0]} 页`;
  return `第 ${pages.join("、")} 页`;
}

function buildLocationText(issue: IssueItem, pageText: string): string {
  const parts: string[] = [];

  if (pageText) parts.push(pageText);

  const fields: Array<[unknown, string]> = [
    [issue.location?.table, "表格"],
    [issue.location?.section, "章节"],
    [issue.location?.row, "行"],
    [issue.location?.col, "列"],
    [issue.location?.field, "字段"],
    [issue.location?.code, "编码"],
    [issue.location?.subject, "科目"],
  ];

  fields.forEach(([value, label]) => {
    const text = cleanText(value);
    if (!text) return;
    parts.push(`${label}：${text}`);
  });

  const firstRef = Array.isArray(issue.location?.table_refs)
    ? issue.location?.table_refs?.find((item) => item && typeof item === "object")
    : null;

  if (parts.length === 0 && firstRef && typeof firstRef === "object") {
    const refParts: string[] = [];
    const refPage = toPositivePage((firstRef as Record<string, unknown>).page);
    if (refPage) refParts.push(`第 ${refPage} 页`);
    [
      ["table", "表格"],
      ["section", "章节"],
      ["row", "行"],
      ["col", "列"],
      ["field", "字段"],
      ["code", "编码"],
      ["subject", "科目"],
    ].forEach(([key, label]) => {
      const value = cleanText((firstRef as Record<string, unknown>)[key]);
      if (!value) return;
      refParts.push(`${label}：${value}`);
    });
    if (refParts.length > 0) {
      return refParts.join(" / ");
    }
  }

  return parts.join(" / ");
}

function getPrimaryEvidenceText(issue: IssueItem): string {
  const firstEvidence = Array.isArray(issue.evidence) ? issue.evidence[0] : null;
  const fromEvidence = cleanText(firstEvidence?.text_snippet) || cleanText(firstEvidence?.text);
  if (fromEvidence) return fromEvidence;
  return cleanText(issue.text_snippet);
}

function collectPages(issue: IssueItem): number[] {
  const values = [
    ...(Array.isArray(issue.location?.pages) ? issue.location.pages : []),
    issue.location?.page,
    issue.evidence?.[0]?.page,
    issue.page_number,
  ];

  const pages = values
    .map((value) => toPositivePage(value))
    .filter((value): value is number => value != null);

  return Array.from(new Set(pages));
}

function normalizeLines(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => cleanText(item)).filter(Boolean);
}

function dedupeLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  lines.forEach((line) => {
    const text = cleanText(line);
    if (!text || seen.has(text)) return;
    seen.add(text);
    result.push(text);
  });
  return result;
}

function humanizeKey(key: string): string {
  const normalized = cleanText(key);
  if (!normalized) return "";
  const aliases: Record<string, string> = {
    amount: "金额",
    diff: "差额",
    difference: "差额",
    page: "页码",
    pages: "页码",
    ratio: "比例",
    rate: "比例",
    table: "表格",
  };
  if (aliases[normalized]) return aliases[normalized];
  return normalized.replace(/[_-]+/g, " ");
}

function formatMetricValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "number") {
    return value.toLocaleString("zh-CN", {
      maximumFractionDigits: 2,
    });
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatMetricValue(item)).filter(Boolean).join("、");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).slice(0, 3);
    return entries
      .map(([key, item]) => `${humanizeKey(key)}=${formatMetricValue(item)}`)
      .join("；");
  }
  return cleanText(value);
}

function firstSentence(value: string | undefined): string {
  const text = cleanText(value);
  if (!text) return "";
  const matched = text.match(/^(.+?[。；;.!?])/);
  return cleanText(matched?.[1]) || text;
}

function toPositivePage(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.floor(parsed);
}

function cleanText(value: unknown): string {
  if (value == null) return "";
  return String(value).trim();
}
