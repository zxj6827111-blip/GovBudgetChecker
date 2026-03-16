import type { JobSummaryRecord } from "@/lib/uiAdapters";

export type DepartmentTab = "budget" | "final" | "review";
export type AdvancedFilters = {
  highRiskOnly: boolean;
  unlinkedOnly: boolean;
  pendingReviewOnly: boolean;
};

export async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok ? ((await response.json()) as T) : fallback;
  } catch {
    return fallback;
  }
}

export async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload?.detail || payload?.error || payload?.message || text || `HTTP ${response.status}`;
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

export function normalizeSearchValue(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

export function needsIngestReview(job: JobSummaryRecord): boolean {
  return Number(job.review_item_count ?? 0) > 0 || String(job.report_kind ?? "").trim().toLowerCase() === "unknown";
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function getDispositionFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
}

export function escapeCsvCell(value: unknown): string {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;
}
