/**
 * 复核生命周期（WP3-A）的前端适配层：接口响应 → 页面可用状态。
 *
 * 为什么单独一层
 * --------------
 * 页面只应该关心"现在能不能完成复核、为什么不能、上一次是谁什么时候完成的"，
 * 不应该关心 ``detail.error`` 长什么样、``blockers`` 怎么解析。把这层收在这里，
 * 页面组件就能保持"渲染 + 交互"，而契约解析有**唯一**实现处（与
 * ``materialDetailAdapters`` / ``materialSearchAdapters`` 同一手法）。
 *
 * 三条纪律
 * --------
 * 1. **不猜状态。** 后端说 ``available=false`` 就是没有复核上下文，
 *    前端不能因为"有 job_id"就假装能复核。
 * 2. **失败也说人话。** 409 的业务原因来自 ``detail.blockers``，
 *    界面必须逐条显示；只显示"HTTP 409"等于把唯一有用的信息丢掉。
 * 3. **未识别的码不吞。** 未知阻塞码/失效原因原样带出，便于发现遗漏。
 */

import {
  invalidationReasonLabel,
  reviewStatusLabel,
  reviewStatusTone,
} from "@/lib/reviewLifecyclePresentation";

/** 门禁阻塞。 */
export interface ReviewBlockerRecord {
  code: string;
  count: number | null;
}

/** 完成门禁结论（服务端重算）。 */
export interface ReviewCompletionGateRecord {
  can_complete: boolean;
  blockers: ReviewBlockerRecord[];
}

/** 当前分析（某一份文件版本上的当前一次运行）。 */
export interface ReviewCurrentAnalysisRecord {
  job_uuid: string | null;
  analysis_revision: number | null;
  analysis_basis_token: string | null;
  document_version_id: number | null;
  status: string | null;
  completed: boolean;
  /** null 表示"算不出来"，与 0 = "算过且确实没有问题"严格区分。 */
  formal_issue_count: number | null;
}

/** 复核会话。 */
export interface ReviewSessionRecord {
  review_session_id: string;
  status: string;
  slot_id: string;
  document_version_id: number;
  analysis_job_uuid: string;
  analysis_basis_token: string;
  started_by: string;
  started_at: string;
  completed_by: string | null;
  completed_at: string | null;
  invalidated_at: string | null;
  invalidated_reason: string | null;
  review_result: Record<string, unknown>;
}

/** 历史会话。 */
export interface ReviewHistoryRecord {
  review_session_id: string;
  status: string;
  document_version_id: number;
  analysis_job_uuid: string;
  analysis_basis_token: string;
  started_by: string;
  started_at: string;
  completed_by: string | null;
  completed_at: string | null;
  invalidated_at: string | null;
  invalidated_reason: string | null;
}

/** 复核上下文可用性（按任务解析时使用）。 */
export interface ReviewContextRecord {
  available: boolean;
  reason: string | null;
  slot_id: string | null;
  job_uuid: string | null;
}

/** `GET /api/reviews/{slot_id}` 的数据体。 */
export interface ReviewLifecycleDataRecord {
  slot_id: string;
  current_document_version_id: number | null;
  current_analysis: ReviewCurrentAnalysisRecord;
  current_session: ReviewSessionRecord | null;
  history: ReviewHistoryRecord[];
  completion_gate: ReviewCompletionGateRecord;
}

/** `GET /api/reviews?job_uuid=` 的数据体。 */
export interface ReviewByJobDataRecord {
  review_context: ReviewContextRecord;
  review: ReviewLifecycleDataRecord | null;
}

/**
 * 复核上下文不可用时的中文说明（§七十三）。
 *
 * 这不是错误：旧任务本来就可能没有槽位链路。页面必须显示"看得到但不可复核"，
 * 而不是"出错了"。
 */
export const REVIEW_CONTEXT_UNAVAILABLE_MESSAGE =
  "当前任务尚未建立可持久化的材料复核上下文，仅供查看历史审核内容。";

/** 复核上下文的界面状态：可用 / 不可用 / 还没解析出来。 */
export type ReviewContextState =
  | { kind: "loading" }
  | { kind: "unavailable"; message: string }
  | { kind: "available"; review: ReviewLifecycleDataRecord };

/** 把接口返回体转成页面状态。 */
export function toReviewContextState(
  payload: ReviewByJobDataRecord | null | undefined,
): ReviewContextState {
  if (!payload || !payload.review_context) {
    return { kind: "unavailable", message: REVIEW_CONTEXT_UNAVAILABLE_MESSAGE };
  }
  if (!payload.review_context.available || !payload.review) {
    return { kind: "unavailable", message: REVIEW_CONTEXT_UNAVAILABLE_MESSAGE };
  }
  return { kind: "available", review: payload.review };
}

/** 完成复核按钮的即时提示（真正的判定在服务端）。 */
export interface CompleteButtonState {
  /** 是否允许点击。 */
  enabled: boolean;
  /** 不可点击时的说明（已完成的材料换成"重新复核"）。 */
  hint: string;
  /** 已完成的材料显示"重新复核"而不是"完成复核"。 */
  label: string;
  /** 是否已经完成（页面据此显示复核人/完成时间，而不是"完成复核"按钮）。 */
  completed: boolean;
}

/**
 * 计算「完成复核」按钮的即时状态。
 *
 * `pendingProblemCount` 只是**即时提示**：服务端门禁还会检查版本、分析代际、
 * 身份、口径与检查覆盖，这些前端未必知道。因此按钮 disabled 只用于少让用户
 * 白跑一趟，绝不作为"能不能完成"的依据（§七十八）。
 */
export function resolveCompleteButtonState(
  review: ReviewLifecycleDataRecord | null | undefined,
  pendingProblemCount: number,
): CompleteButtonState {
  if (hasCompletedReviewForCurrentBasis(review)) {
    // 已完成：按钮换成"重新复核"（显式 reopen）。打开页面**不会**自动把
    // completed 改回 reviewing——那会让"这份材料复核过"这一事实凭空消失（§七十六）。
    return { enabled: true, hint: "", label: "重新复核", completed: true };
  }
  if (review?.completion_gate?.can_complete) {
    return { enabled: true, hint: "", label: "完成复核", completed: false };
  }
  if (pendingProblemCount > 0) {
    return {
      enabled: false,
      hint: "还有待处理问题，无法完成复核",
      label: "完成复核",
      completed: false,
    };
  }
  return {
    enabled: false,
    hint: "当前无法完成复核（详见下方原因）",
    label: "完成复核",
    completed: false,
  };
}

/** 已完成复核的摘要（复核人 + 完成时间）。 */
export function describeCompletedReview(
  session: ReviewSessionRecord | ReviewHistoryRecord | null | undefined,
): string | null {
  if (!session || session.status !== "completed") {
    return null;
  }
  const who = String(session.completed_by ?? "").trim() || "未知复核人";
  const when = formatReviewMoment(session.completed_at);
  return when ? `${who} · ${when}` : who;
}

/** 已完成会话来自历史时的摘要（当前会话为空、最近一条历史是 completed）。 */
export function latestCompletedSession(
  review: ReviewLifecycleDataRecord | null | undefined,
): ReviewSessionRecord | ReviewHistoryRecord | null {
  if (!review) {
    return null;
  }
  if (review.current_session?.status === "completed") {
    return review.current_session;
  }
  return review.history.find((item) => item.status === "completed") ?? null;
}

/** 最近一次失效记录（用于展示"为什么需要重新复核"）。 */
export function latestInvalidatedSession(
  review: ReviewLifecycleDataRecord | null | undefined,
): ReviewHistoryRecord | null {
  if (!review) {
    return null;
  }
  return review.history.find((item) => item.status === "invalidated") ?? null;
}

/**
 * 当前分析代际上是否已有**已完成**的复核。
 *
 * 必须带代际比较，不能只看"历史里有没有 completed"：
 * 上一代结果的完成记录与这一代无关，用它来判断"这份材料复核过了"
 * 等于拿上一份分析的结论冒充这一份（§十六）。
 */
export function hasCompletedReviewForCurrentBasis(
  review: ReviewLifecycleDataRecord | null | undefined,
): boolean {
  if (!review) {
    return false;
  }
  if (review.current_session?.status === "completed") {
    return true;
  }
  const basis = String(review.current_analysis?.analysis_basis_token ?? "").trim();
  if (!basis) {
    return false;
  }
  return review.history.some(
    (item) => item.status === "completed" && String(item.analysis_basis_token ?? "").trim() === basis,
  );
}

/**
 * 进入工作台时是否应当自动开始复核（§七十四）。
 *
 * 四个条件同时成立才自动开：有可复核的当前分析、有活动会话之外的空位
 * （即当前没有进行中的会话）、当前代际上还没有完成记录。
 *
 * **绝不自动完成**（§三十三）：进入工作台只是"准备复核"，
 * 完成必须由人显式点击。
 */
export function shouldAutoStartReview(
  review: ReviewLifecycleDataRecord | null | undefined,
): boolean {
  if (!review) {
    return false;
  }
  if (!review.current_analysis?.completed) {
    return false;
  }
  if (review.current_session) {
    return false;
  }
  return !hasCompletedReviewForCurrentBasis(review);
}

/** 顶部复核徽章：色调取值域与 Badge 组件一致（不得自造 tone）。 */
export interface ReviewBadgeRecord {
  tone: "review" | "processing" | "lowconf" | "failed" | "done" | "neutral";
  label: string;
}

/** 顶部状态徽章：会话状态 + 失效原因。 */
export function resolveReviewBadge(
  review: ReviewLifecycleDataRecord | null | undefined,
): ReviewBadgeRecord | null {
  if (!review) {
    return null;
  }
  const session = review.current_session;
  if (session) {
    return { tone: reviewStatusTone(session.status), label: reviewStatusLabel(session.status) };
  }
  const invalidated = latestInvalidatedSession(review);
  if (invalidated) {
    const reason = invalidationReasonLabel(invalidated.invalidated_reason);
    return {
      tone: reviewStatusTone(invalidated.status),
      label: reason ? `复核已失效 · ${reason}` : "复核已失效",
    };
  }
  const completed = latestCompletedSession(review);
  if (completed) {
    return { tone: reviewStatusTone(completed.status), label: reviewStatusLabel(completed.status) };
  }
  return null;
}

/** ISO 时间 → 本地 `YYYY-MM-DD HH:mm`；解析不出来时返回 null（不猜）。 */
export function formatReviewMoment(value: string | null | undefined): string | null {
  const text = String(value ?? "").trim();
  if (!text) {
    return null;
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  const pad = (input: number) => String(input).padStart(2, "0");
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}` +
    ` ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  );
}

/** 后端业务错误体（409 的 detail 是对象，不是字符串）。 */
export interface ReviewErrorDetail {
  error: string;
  message: string;
  blockers?: ReviewBlockerRecord[];
}

/** 从响应体里解析业务错误体；不是本形态时返回 null。 */
export function parseReviewErrorDetail(body: unknown): ReviewErrorDetail | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return null;
  }
  const detail = (body as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return null;
  }
  const record = detail as Record<string, unknown>;
  const error = String(record.error ?? "").trim();
  if (!error) {
    return null;
  }
  const rawBlockers = Array.isArray(record.blockers) ? record.blockers : [];
  return {
    error,
    message: String(record.message ?? "").trim() || "操作未完成",
    blockers: rawBlockers
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
      .map((item) => ({
        code: String(item.code ?? "").trim(),
        count: typeof item.count === "number" && Number.isFinite(item.count) ? item.count : null,
      })),
  };
}
