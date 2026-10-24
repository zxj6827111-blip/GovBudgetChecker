import { createSharedListRequest } from "./listRequests";

const pendingReads = new Map<string, { promise: Promise<unknown>; reset: () => void }>();

/** 严格模式/轮询同时读取同一资源时只发一次；响应结束即释放，不缓存复核状态。 */
export function readReviewJson<T>(url: string): Promise<T> {
  const pending = pendingReads.get(url);
  if (pending) return pending.promise as Promise<T>;
  const request = createSharedListRequest<unknown>(async signal => {
    const response = await fetch(url, { cache: "no-store", signal });
    if (!response.ok) throw new Error(`复核数据读取失败（HTTP ${response.status}）`);
    return response.json();
  });
  const entry = { promise: request.load().finally(() => {
    if (pendingReads.get(url) === entry) pendingReads.delete(url);
  }), reset: request.reset };
  pendingReads.set(url, entry);
  return entry.promise as Promise<T>;
}

/** 写入成功后取消此前尚未结束的读取，旧状态不能覆盖新决定。 */
export function invalidateReviewReads(jobId: string) {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("govbudgetchecker:material-reports-changed"));
  const job = encodeURIComponent(jobId);
  for (const [url, entry] of Array.from(pendingReads)) {
    if (url === `/api/jobs/${job}` || url.startsWith(`/api/jobs/${job}/`)
      || url === `/api/reviews?job_uuid=${job}` || url === `/api/workflow?job_id=${job}`) {
      pendingReads.delete(url);
      entry.reset();
    }
  }
}
