export function normalizeBackendError(data: any, status: number): any {
  const detail = String(data?.detail || data?.error || data?.message || "");
  if (status === 429 || /too many requests/i.test(detail)) {
    return { ...data, detail: "当前操作请求过于频繁，后端暂时限流。请稍等一分钟后重试。" };
  }
  return data;
}