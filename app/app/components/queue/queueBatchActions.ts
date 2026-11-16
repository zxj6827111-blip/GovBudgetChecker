import { normalizeUiTaskStatus, type JobSummaryRecord } from "../../../lib/uiAdapters";
import { ANALYZE_START_REQUEST_BODY } from "../workspace/uploadCenterAdapters";
import {
  DEFAULT_REANALYZE_METHOD,
  buildReanalyzeRequestBody,
  inheritanceWarningsFromResponse,
  type ReanalyzeMethodValue,
} from "../reanalyzeOptions";

export interface QueueRunFailure {
  jobId: string;
  filename: string;
  message: string;
}

/** 提交成功但模型配置已变化等需要用户知晓的继承告警。 */
export interface QueueRunOutcome {
  warnings: string[];
}

export interface QueueRunWarning {
  jobId: string;
  filename: string;
  messages: string[];
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
  warnings: QueueRunWarning[];
}

export function canRunQueueJob(job: JobSummaryRecord): boolean {
  return Boolean(String(job.job_id ?? "").trim()) && normalizeUiTaskStatus(job.status) !== "analyzing";
}

function extractPayloadMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const body = payload as Record<string, unknown>;
  const detail = body.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object" && typeof (detail as Record<string, unknown>).message === "string") {
    return String((detail as Record<string, unknown>).message);
  }
  if (typeof body.error === "string" && body.error) return body.error;
  if (typeof body.message === "string" && body.message) return body.message;
  return null;
}

function requestErrorMessage(status: number, payload: unknown): string {
  if (status === 409) return "任务已在运行或状态已变化，请刷新队列后重试。";
  if (status === 401 || status === 403) return "登录已失效或没有权限操作此任务。";
  if (status === 404) return "任务或原始 PDF 已不存在。";
  return extractPayloadMessage(payload) ?? `提交未成功（HTTP ${status}）。`;
}

function deleteErrorMessage(status: number, payload: unknown): string {
  if (status === 401 || status === 403) return "登录已失效或只有管理员可以删除任务。";
  if (status === 404) return "任务已不存在，请刷新队列后确认。";
  if (status === 409) {
    return extractPayloadMessage(payload) ?? "任务正在运行中，请等待完成或取消后再删除。";
  }
  return extractPayloadMessage(payload) ?? `删除未成功（HTTP ${status}）。`;
}

/** 与行内按钮共用请求：待分析任务启动首次分析，其他可运行任务原地重新分析。
 *  重分析默认按上次检查方式逐条继承；method 由统一选择对话框显式传入。 */
export async function requestQueueRun(
  job: JobSummaryRecord,
  fetcher: typeof fetch = fetch,
  method: ReanalyzeMethodValue = DEFAULT_REANALYZE_METHOD,
): Promise<QueueRunOutcome> {
  if (!canRunQueueJob(job)) throw new Error("任务正在运行或状态未识别，已跳过。");
  const id = encodeURIComponent(String(job.job_id));
  const initial = normalizeUiTaskStatus(job.status) === "pending_analysis";
  let response: Response;
  try {
    response = await fetcher(initial ? `/api/analyze/${id}` : `/api/jobs/${id}/reanalyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(initial ? ANALYZE_START_REQUEST_BODY : buildReanalyzeRequestBody(method)),
    });
  } catch {
    throw new Error("网络请求失败，请检查连接后重试。");
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok || (payload && typeof payload === "object" && (payload as Record<string, unknown>).success === false)) {
    throw new Error(requestErrorMessage(response.status, payload));
  }
  return { warnings: inheritanceWarningsFromResponse(payload) };
}

/** 仅限制提交并发；真正的分析由后端任务队列调度，不把提交成功误报为分析完成。 */
export async function runQueueBatch(
  jobs: JobSummaryRecord[],
  submit: (job: JobSummaryRecord) => Promise<QueueRunOutcome | void>,
  onProgress: (progress: QueueBatchProgress) => void,
): Promise<QueueBatchResult> {
  const unique = Array.from(new Map(jobs.map((job) => [String(job.job_id ?? ""), job])).values());
  const result: QueueBatchResult = { submittedIds: [], failures: [], warnings: [] };
  let nextIndex = 0;
  let completed = 0;
  const report = () => onProgress({total: unique.length, completed, submitted: result.submittedIds.length, failed: result.failures.length});
  report();
  async function worker() {
    while (nextIndex < unique.length) {
      const job = unique[nextIndex++];
      try {
        if (!canRunQueueJob(job)) throw new Error("任务正在运行或状态未识别，已跳过。");
        const outcome = await submit(job);
        result.submittedIds.push(String(job.job_id));
        if (outcome && outcome.warnings.length > 0) {
          result.warnings.push({
            jobId: String(job.job_id),
            filename: String(job.filename ?? job.job_id ?? ""),
            messages: outcome.warnings,
          });
        }
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

export interface QueueDeleteResult {
  ok: boolean;
  jobId: string;
  deleted: boolean;
  deletedSlotIds: string[];
  resetBaselineSlotIds: string[];
  updatedSlotIds: string[];
  deletedVersionIds: number[];
  error?: string;
}

export interface QueueBatchDeleteFailure {
  job_id?: string;
  slot_id?: string;
  status_code?: number;
  detail?: string;
}

export interface QueueBatchDeleteResult {
  ok: boolean;
  requestedCount: number;
  deletedCount: number;
  failedCount: number;
  deletedJobIds: string[];
  failed: QueueBatchDeleteFailure[];
  deletedSlotIds: string[];
  resetBaselineSlotIds: string[];
  updatedSlotIds: string[];
  deletedVersionIds: number[];
  error?: string;
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item ?? "")).filter(Boolean) : [];
}

function toNumberArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => Number(item)).filter((num) => Number.isFinite(num));
}

function normalizeJobIds(jobIds: readonly string[]): string[] {
  return Array.from(new Set(jobIds.map((id) => String(id ?? "").trim()).filter(Boolean)));
}

/** 删除单个任务及其关联记录（后端限管理员，运行中的任务会返回 409）。 */
export async function requestQueueDelete(
  jobId: string,
  fetcher: typeof fetch = fetch,
): Promise<QueueDeleteResult> {
  const normalizedId = String(jobId ?? "").trim();
  const baseFailure = {
    ok: false as const,
    jobId: normalizedId,
    deleted: false,
    deletedSlotIds: [],
    resetBaselineSlotIds: [],
    updatedSlotIds: [],
    deletedVersionIds: [],
  };
  if (!normalizedId) {
    return { ...baseFailure, error: "缺少任务 ID，无法删除。" };
  }
  let response: Response;
  try {
    response = await fetcher(`/api/jobs/${encodeURIComponent(normalizedId)}`, { method: "DELETE" });
  } catch {
    return { ...baseFailure, error: "网络请求失败，请检查连接后重试。" };
  }
  const payload: unknown = await response.json().catch(() => null);
  const body = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  if (!response.ok || body.success === false) {
    return { ...baseFailure, error: deleteErrorMessage(response.status, payload) };
  }
  return {
    ok: true,
    jobId: String(body.job_id ?? normalizedId),
    deleted: true,
    deletedSlotIds: toStringArray(body.deleted_slot_ids),
    resetBaselineSlotIds: toStringArray(body.reset_baseline_slot_ids),
    updatedSlotIds: toStringArray(body.updated_slot_ids),
    deletedVersionIds: toNumberArray(body.deleted_version_ids),
  };
}

/** 批量删除任务及其关联记录（支持部分成功返回 deletedJobIds 与 failed 明细）。 */
export async function requestQueueBatchDelete(
  jobIds: readonly string[],
  fetcher: typeof fetch = fetch,
): Promise<QueueBatchDeleteResult> {
  const normalizedIds = normalizeJobIds(jobIds);
  const emptyResult: QueueBatchDeleteResult = {
    ok: false,
    requestedCount: normalizedIds.length,
    deletedCount: 0,
    failedCount: 0,
    deletedJobIds: [],
    failed: [],
    deletedSlotIds: [],
    resetBaselineSlotIds: [],
    updatedSlotIds: [],
    deletedVersionIds: [],
  };
  if (normalizedIds.length === 0) {
    return { ...emptyResult, error: "未选择任何可删除的任务。" };
  }
  let response: Response;
  try {
    response = await fetcher("/api/jobs/batch-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: normalizedIds }),
    });
  } catch {
    return { ...emptyResult, error: "网络请求失败，请检查连接后重试。" };
  }
  const payload: unknown = await response.json().catch(() => null);
  const body = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  if (!response.ok) {
    return { ...emptyResult, error: deleteErrorMessage(response.status, payload) };
  }
  const deletedJobIds = toStringArray(body.deleted_job_ids);
  const failed = Array.isArray(body.failed) ? (body.failed as QueueBatchDeleteFailure[]) : [];
  const requestedCount = typeof body.requested_count === "number" ? body.requested_count : normalizedIds.length;
  const deletedCount = typeof body.deleted_count === "number" ? body.deleted_count : deletedJobIds.length;
  const failedCount = typeof body.failed_count === "number" ? body.failed_count : failed.length;
  const deletedSlotIds = toStringArray(body.deleted_slot_ids);
  const resetBaselineSlotIds = toStringArray(body.reset_baseline_slot_ids);
  const updatedSlotIds = toStringArray(body.updated_slot_ids);
  const deletedVersionIds = toNumberArray(body.deleted_version_ids);

  const succeeded = deletedCount > 0 || (failedCount === 0 && body.success !== false);
  const errorMessage = !succeeded
    ? (failed[0]?.detail || extractPayloadMessage(payload) || "批量删除未成功，请刷新队列后重试。")
    : undefined;

  return {
    ok: succeeded,
    requestedCount,
    deletedCount,
    failedCount,
    deletedJobIds,
    failed,
    deletedSlotIds,
    resetBaselineSlotIds,
    updatedSlotIds,
    deletedVersionIds,
    ...(errorMessage ? { error: errorMessage } : {}),
  };
}
