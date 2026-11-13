import type { JobSummaryRecord } from "./uiAdapters";

export const JOB_LIST_UPDATED_EVENT = "govbudgetchecker:job-list-updated";

/** Share the queue's latest snapshot with navigation without a second fetch. */
export function publishJobList(jobs: JobSummaryRecord[]) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(JOB_LIST_UPDATED_EVENT, { detail: jobs }));
  }
}

export function subscribeJobList(listener: (jobs: JobSummaryRecord[]) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handle = (event: Event) => {
    const jobs = (event as CustomEvent<unknown>).detail;
    if (Array.isArray(jobs)) listener(jobs);
  };
  window.addEventListener(JOB_LIST_UPDATED_EVENT, handle);
  return () => window.removeEventListener(JOB_LIST_UPDATED_EVENT, handle);
}
