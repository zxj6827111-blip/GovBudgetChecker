import { fetchJobCounts, type JobCountsView } from "./jobListViews";
import type { ReviewListData } from "../app/components/review-workbench/reviewListAdapters";

/** 同时挂载的侧栏/清单共用正在进行的请求；不保存已完成响应，避免跨会话复用。 */
export function createSharedListRequest<T>(request: (signal: AbortSignal) => Promise<T>, timeoutMs = 15_000) {
  let pending: { promise: Promise<T>; controller: AbortController } | null = null;
  return {
    load(): Promise<T> {
      if (pending) return pending.promise;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const entry = { controller, promise: Promise.resolve().then(() => request(controller.signal)).finally(() => {
        clearTimeout(timer);
        if (pending === entry) pending = null;
      }) };
      pending = entry;
      return entry.promise;
    },
    reset() {
      pending?.controller.abort();
      pending = null;
    },
  };
}

/** 侧栏角标只要计数：不再为一个数字下载全部任务摘要。 */
export const jobCountsRequest = createSharedListRequest<JobCountsView>(signal => fetchJobCounts(signal));

export const reviewWorklistRequest = createSharedListRequest<ReviewListData>(async signal => {
  const response = await fetch("/api/reviews/worklist", { cache: "no-store", signal });
  if (!response.ok) throw new Error(response.status === 401 ? "登录已失效，请重新登录。" : "复核清单暂时不可用，请检查服务后重试。");
  const payload = await response.json() as { data: ReviewListData };
  if (!Array.isArray(payload.data?.items) || typeof payload.data.pending_total !== "number") throw new Error("复核清单数据不完整，请重试。");
  return payload.data;
});
