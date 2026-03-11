import type { IssueItem } from "../components/IssueTabs";

export type PreviewTarget = {
  page: number;
  bbox?: number[];
};

export type LocationPreviewRef = {
  key: string;
  role: string;
  page: number;
  bbox?: number[];
  target: PreviewTarget;
  locationText: string;
  valueText?: string;
};

export function toPositivePage(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.floor(parsed);
}

export function normalizeBbox(raw: unknown): number[] | undefined {
  if (!Array.isArray(raw) || raw.length !== 4) return undefined;
  const values = raw.map((item) => Number(item));
  if (values.some((item) => !Number.isFinite(item))) return undefined;
  if (values[2] <= values[0] || values[3] <= values[1]) return undefined;
  return values;
}

export function getPrimaryPage(issue: IssueItem): number | null {
  const values = [
    ...(Array.isArray(issue.location?.pages) ? issue.location.pages : []),
    issue.location?.page,
    issue.evidence?.[0]?.page,
    issue.page_number,
  ];

  for (const value of values) {
    const page = toPositivePage(value);
    if (page) return page;
  }
  return null;
}

export function getPreviewTarget(issue: IssueItem): PreviewTarget | null {
  const refTarget = getLocationPreviewRefs(issue).find((item) => item.bbox)?.target;
  if (refTarget) return refTarget;

  if (Array.isArray(issue.evidence)) {
    for (const evidence of issue.evidence) {
      const page = toPositivePage(evidence?.page);
      const bbox = normalizeBbox(evidence?.bbox);
      if (page && bbox) {
        return { page, bbox };
      }
    }
  }

  const page = getPrimaryPage(issue);
  const bbox = normalizeBbox(issue.bbox);
  if (!page) return null;
  return bbox ? { page, bbox } : { page };
}

export function getLocationPreviewRefs(issue: IssueItem): LocationPreviewRef[] {
  const rawRefs = Array.isArray(issue.location?.table_refs) ? issue.location.table_refs : [];
  const refs: LocationPreviewRef[] = [];

  rawRefs.forEach((rawRef, index) => {
    if (!rawRef || typeof rawRef !== "object") return;

    const ref = rawRef as Record<string, unknown>;
    const page = toPositivePage(ref.page);
    if (!page) return;

    const bbox = normalizeBbox(ref.bbox);
    const role = cleanText(ref.role) || `定位 ${index + 1}`;
    const locationText = buildRefLocationText(ref, page);
    const valueText = buildRefValueText(ref.value);

    refs.push({
      key: `${role}|${page}|${index}`,
      role,
      page,
      bbox,
      target: bbox ? { page, bbox } : { page },
      locationText,
      valueText,
    });
  });

  return refs;
}

export function buildPdfPageUrl(jobId?: string, page?: number | null): string | null {
  if (!jobId || !page) return null;
  return `/api/files/${jobId}/source#page=${page}`;
}

export function buildPreviewUrl(issue: IssueItem, target: PreviewTarget | null): string | null {
  if (!issue.job_id || !target?.page) return null;

  const params = new URLSearchParams({
    page: String(target.page),
    padding: target.bbox ? "28" : "0",
    scale: target.bbox ? "2" : "1.2",
  });

  if (target.bbox) {
    params.set("bbox", target.bbox.join(","));
  }

  return `/api/files/${issue.job_id}/preview?${params.toString()}`;
}

export function buildIssueViewerUrl(
  issue: IssueItem,
  target: PreviewTarget | null,
  extras?: {
    title?: string;
    location?: string;
  }
): string | null {
  if (!issue.job_id || !target?.page) return null;

  const params = new URLSearchParams({
    page: String(target.page),
  });

  if (target.bbox) {
    params.set("bbox", target.bbox.join(","));
  }
  if (extras?.title) {
    params.set("title", extras.title);
  }
  if (extras?.location) {
    params.set("location", extras.location);
  }

  return `/viewer/${issue.job_id}?${params.toString()}`;
}

function buildRefLocationText(ref: Record<string, unknown>, page: number): string {
  const parts = [`第 ${page} 页`];

  const labels: Array<[string, string]> = [
    ["table", "表格"],
    ["section", "章节"],
    ["row", "行"],
    ["col", "列"],
    ["field", "字段"],
    ["code", "编码"],
    ["subject", "科目"],
  ];

  labels.forEach(([key, label]) => {
    const value = cleanText(ref[key]);
    if (!value) return;
    parts.push(`${label}：${value}`);
  });

  return parts.join(" / ");
}

function buildRefValueText(value: unknown): string | undefined {
  if (value == null || value === "") return undefined;
  if (typeof value === "number") {
    return value.toLocaleString("zh-CN", {
      maximumFractionDigits: 2,
    });
  }
  return cleanText(value) || undefined;
}

function cleanText(value: unknown): string {
  if (value == null) return "";
  return String(value).trim();
}
