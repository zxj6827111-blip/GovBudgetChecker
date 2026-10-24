import { normalizeUiTaskStatus, type JobSummaryRecord } from "../../../lib/uiAdapters";
import { ANALYZE_START_REQUEST_BODY } from "../workspace/uploadCenterAdapters";

export interface QueueRunFailure {
  jobId: string;
  filename: string;
  message: string;
}

export interface QueueBatchProgress {
  total: number;
  completed: number;
  submitted: number;
  failed: number;
}

export interface QueueBatchResult {
  submittedIds: string[];
  failures: QueueRunFailure[];
}

export function canRunQueueJob(job: JobSummaryRecord): boolean {
  return Boolean(String(job.job_id ?? "").trim()) && normalizeUiTaskStatus(job.status) !== "analyzing";
}

function requestErrorMessage(status: number, payload: unknown): string {
  if (status === 409) return "任务已在运行或状态已变化，请刷新队列后重试。";
  if (status === 401 || status === 403) return "登录已失效或没有权限操作此任务。";
  if (status === 404) return "任务或原始 PDF 已不存在。";
  if (payload && typeof payload === "object") {
    const body = payload as Record<string, unknown>;
    const detail = body.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object" && typeof (detail as Record<string, unknown>).message === "string") {
      return String((detail as Record<string, unknown>).message);
    }
    if (typeof body.error === "string" && body.error) return body.error;
    if (typeof body.message === "string" && body.message) return body.message;
  }
  return `提交未成功（HTTP ${status}）。`;
}

/** 与行内按钮共用请求：待分析任务启动首次分析，其他可运行任务原地重新分析。 */
export async function requestQueueRun(job: JobSummaryRecord, fetcher: typeof fetch = fetch): Promise<void> {
  if (!canRunQueueJob(job)) throw new Error("任务正在运行或状态未识别，已跳过。");
  const id = encodeURIComponent(String(job.job_id));
  const initial = normalizeUiTaskStatus(job.status) === "pending_analysis";
  let response: Response;
  try {
    response = await fetcher(initial ? `/api/analyze/${id}` : `/api/jobs/${id}/reanalyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(initial ? ANALYZE_START_REQUEST_BODY : { mode: "dual", use_local_rules: true }),
    });
  } catch {
    throw new Error("网络请求失败，请检查连接后重试。");
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || (payload && typeof payload === "object" && (payload as Record<string, unknown>).success === false)) {
    throw new Error(requestErrorMessage(response.status, payload));
  }
}

/** 仅限制提交并发；真正的分析由后端任务队列调度，不把提交成功误报为分析完成。 */
export async function runQueueBatch(
  jobs: JobSummaryRecord[],
  submit: (job: JobSummaryRecord) => Promise<void>,
  onProgress: (progress: QueueBatchProgress) => void,
): Promise<QueueBatchResult> {
  const unique = Array.from(new Map(jobs.map((job) => [String(job.job_id ?? ""), job])).values());
  const result: QueueBatchResult = { submittedIds: [], failures: [] };
  let nextIndex = 0;
  let completed = 0;
  const report = () => onProgress({total: unique.length, completed, submitted: result.submittedIds.length, failed: result.failures.length});
  report();
  async function worker() {
    while (nextIndex < unique.length) {
      const job = unique[nextIndex++];
      try {
        if (!canRunQueueJob(job)) throw new Error("任务正在运行或状态未识别，已跳过。");
        await submit(job);
        result.submittedIds.push(String(job.job_id));
      } catch (error) {
        result.failures.push({jobId: String(job.job_id ?? ""), filename: String(job.filename ?? job.job_id ?? ""), message: error instanceof Error ? error.message : "提交未成功，请重试。"});
      }
      completed += 1;
      report();
    }
  }
  await Promise.all(Array.from({length: Math.min(3, unique.length)}, () => worker()));
  return result;
}
