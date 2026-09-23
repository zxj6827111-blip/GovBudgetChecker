/**
 * 复核生命周期（WP3-A）的展示层：状态码 → 中文文案。
 *
 * 为什么中文只在展示层
 * --------------------
 * 与 WP2 的纪律一致：后端输出机器可读的状态码与原因码，中文映射在这一层。
 * 后端翻译会让同一个原因在两端各写一份，必然漂移；而"两侧对同一个阻塞码
 * 给出不同解释"正是用户最不能容忍的那类不一致。
 *
 * 三条文案纪律
 * ------------
 * 1. **说清"下一步做什么"**，不是只说"失败"。用户看到的必须是
 *    「材料文件已更新，请重新复核」这种能照着做的句子，而不是「HTTP 409」。
 * 2. **数字来自服务端**。`count` 一律用服务端返回的条数，不在前端另算：
 *    前端算出来的数字一旦与服务端门禁不一致，用户就会对着两个不同的数字做判断。
 * 3. **未知码不吞掉**。没登记过的码原样显示出来（便于发现遗漏），
 *    而不是退化成"操作失败"。
 */

/** 复核会话状态。 */
export const REVIEW_STATUS_IN_PROGRESS = "in_progress";
export const REVIEW_STATUS_COMPLETED = "completed";
export const REVIEW_STATUS_INVALIDATED = "invalidated";

/** 会话状态的中文名。 */
export function reviewStatusLabel(status: string | null | undefined): string {
  const key = String(status ?? "").trim();
  if (key === REVIEW_STATUS_IN_PROGRESS) {
    return "复核中";
  }
  if (key === REVIEW_STATUS_COMPLETED) {
    return "已完成复核";
  }
  if (key === REVIEW_STATUS_INVALIDATED) {
    return "复核已失效";
  }
  return key || "未开始复核";
}

/** 复核会话状态对应的界面色调（与既有 Badge tone 取值域一致）。 */
export function reviewStatusTone(
  status: string | null | undefined,
): "review" | "processing" | "lowconf" | "failed" | "done" | "neutral" {
  const key = String(status ?? "").trim();
  if (key === REVIEW_STATUS_IN_PROGRESS) {
    return "processing";
  }
  if (key === REVIEW_STATUS_COMPLETED) {
    return "done";
  }
  if (key === REVIEW_STATUS_INVALIDATED) {
    // 失效用 `review` 而不是 `lowconf`：它的语义是"需要人重新复核"，
    // 与"这份材料待人工复核"是同一件事；用 lowconf 会让人误以为是
    // "识别置信度低"。
    return "review";
  }
  return "neutral";
}

/** 失效原因的中文说明。 */
export function invalidationReasonLabel(reason: string | null | undefined): string {
  const key = String(reason ?? "").trim();
  if (!key) {
    return "";
  }
  return (
    {
      document_version_changed: "材料文件已更新，请重新复核",
      analysis_restarted: "分析结果已更新，需要重新复核",
      analysis_basis_changed: "分析结果已更新，需要重新复核",
      review_reopened: "复核已重新开始",
    }[key] ?? key
  );
}

/** 门禁阻塞码的中文说明。带条数的阻塞按"还有 N 项…"的口径渲染。 */
export function blockerLabel(code: string, count?: number | null): string {
  const key = String(code ?? "").trim();
  const hasCount = typeof count === "number" && Number.isFinite(count);
  const n = hasCount ? count : 0;

  // 计数类阻塞单列：它们的文案必须把数字说出来。"还有 2 项问题待处理"
  // 与"当前无法完成复核"对用户的价值完全不同。
  if (key === "pending_findings" && hasCount) {
    return `还有 ${n} 项问题待处理`;
  }
  if (key === "needs_review_findings" && hasCount) {
    return `还有 ${n} 项问题标记为待复核`;
  }
  if (key === "blocking_obligations" && hasCount) {
    // 忠实说明能力边界（§八十一）：WP3-A 还没有人工补核入口，
    // 不假装"处理完问题就能完成"。
    return `还有 ${n} 项检查义务未完成（需人工补核，该能力将在人工补核流程中处理）`;
  }

  return (
    {
      document_version_changed: "材料文件已更新，请重新复核",
      analysis_basis_changed: "分析结果已更新，请重新复核",
      identity_unresolved: "材料身份尚未确认（年度/文种/主体待确认）",
      caliber_conflict: "材料口径识别结果互相矛盾，待人工裁决",
      pending_findings: "还有问题待处理",
      needs_review_findings: "还有问题标记为待复核",
      coverage_unavailable: "检查覆盖不可用，无法证明已完成检查",
      blocking_obligations: "存在需要人工补核的检查义务",
      analysis_unavailable: "当前版本还没有可复核的分析结果",
      analysis_not_completed: "当前分析尚未完成",
      no_current_document_version: "这条材料还没有当前文件版本",
      review_already_completed: "这份材料已经完成复核",
      review_not_started: "尚未开始复核",
    }[key] ?? key
  );
}

/** 阻塞列表 → 可直接渲染的中文句子（顺序沿用服务端给出的顺序）。 */
export function describeBlockers(
  blockers: ReadonlyArray<{ code: string; count?: number | null }> | null | undefined,
): string[] {
  if (!Array.isArray(blockers)) {
    return [];
  }
  return blockers.map((item) => blockerLabel(item?.code ?? "", item?.count ?? null));
}

/**
 * 门禁阻塞里"材料/分析已更新"这一类：它不是内容问题，而是事实过期，
 * 用户必须先重新复核，界面因此需要换一种提示（不是"去处理问题"）。
 */
export const STALE_FACT_BLOCKER_CODES = [
  "document_version_changed",
  "analysis_basis_changed",
] as const;

export function isStaleFactBlocker(code: string): boolean {
  return (STALE_FACT_BLOCKER_CODES as readonly string[]).includes(String(code ?? "").trim());
}
