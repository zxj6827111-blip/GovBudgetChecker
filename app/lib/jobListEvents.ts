import type { JobCountsView } from "./jobListViews";

export const JOB_COUNTS_UPDATED_EVENT = "govbudgetchecker:job-counts-updated";

/** 处理队列每次刷新都带回全部任务的计数，转给侧栏角标，侧栏不必再单独请求。 */
export function publishJobCounts(counts: JobCountsView) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(JOB_COUNTS_UPDATED_EVENT, { detail: counts }));
  }
}

export function subscribeJobCounts(listener: (counts: JobCountsView) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handle = (event: Event) => {
    const counts = (event as CustomEvent<unknown>).detail as Partial<JobCountsView> | null;
    if (counts && typeof counts.total === "number" && counts.status_counts) listener(counts as JobCountsView);
  };
  window.addEventListener(JOB_COUNTS_UPDATED_EVENT, handle);
  return () => window.removeEventListener(JOB_COUNTS_UPDATED_EVENT, handle);
}
