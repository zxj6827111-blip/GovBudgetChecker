/** 文件侧分析版本与数据库整数代际是两种身份，不能互相转换。 */
export function fileAnalysisRevision(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  const revision = value.trim();
  if (revision.startsWith(".") || revision.includes("/") || revision.includes("\\")) {
    throw new Error("分析版本标识无效，请重新加载报告");
  }
  return revision;
}

export function reportDownloadUrl(jobId: string, format: string, revision?: string): string {
  const params = new URLSearchParams({ job_id: jobId, format });
  const bound = fileAnalysisRevision(revision);
  if (bound) params.set("analysis_revision", bound);
  return `/api/reports/download?${params.toString()}`;
}

export function displayedAnalysisRevision(job: Record<string, unknown>): string | undefined {
  const snapshot = job.fact_snapshot;
  // 重分析进行中时仍可能显示上一版报告；以实际显示的事实快照为准。
  if (snapshot && typeof snapshot === "object" && "revision_id" in snapshot) {
    const revision = fileAnalysisRevision(snapshot.revision_id);
    if (revision) return revision;
  }
  return fileAnalysisRevision(job.analysis_revision);
}

export function recognitionStateLabel(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const state = value as Record<string, unknown>;
  const recovered = state.source_bound_cells;
  const pending = state.pending_adjudication_cells;
  const pages = state.pending_processing_pages;
  if (typeof recovered !== "number" || !Number.isSafeInteger(recovered) || recovered < 0
    || typeof pending !== "number" || !Number.isSafeInteger(pending) || pending < 0
    || !Array.isArray(pages)) return undefined;
  const storage = state.storage_consistent === true ? "存储一致" : "存储待核对";
  return `已恢复 ${recovered} 格 · 待裁定 ${pending} 格 · 待处理 ${pages.length} 页 · ${storage}`;
}
