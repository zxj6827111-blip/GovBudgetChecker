import assert from "node:assert/strict";

import {
  blockerLabel,
  obligationDecisionLabel,
  obligationDecisionTone,
} from "../lib/reviewLifecyclePresentation";
import {
  isObligationHandled,
  pendingObligationCount,
  toReviewContextState,
  type ObligationReviewItemRecord,
  type ReviewLifecycleDataRecord,
} from "../app/components/review-workbench/reviewLifecycleAdapters";

/**
 * 人工补核（WP3-B）的前端纯逻辑契约。
 *
 * 红线与后端一一对应：
 * - 已处理只有三个词：verified_ok / verified_issue / not_applicable；
 *   "没有记录"、pending、拼错的值，全部按待处理——拼错的状态码不许静默放行；
 * - 覆盖不可用 / 字段缺失 ⇒ 待办数是 null，不是 0（"没有覆盖记录"与
 *   "0 项待补核"在数字上不能长得一样）；
 * - 阻塞文案必须指路到「检查补核」页签，而不是只说"不能完成"。
 */

function item(overrides: Partial<ObligationReviewItemRecord> = {}): ObligationReviewItemRecord {
  return {
    obligation_id: "OBL-A",
    group_id: "TABLE_CROSS",
    group_title: "表间关系",
    title: "义务 A",
    status: "not_executed",
    reason: "not_executed",
    reason_label: "未执行",
    detail: "",
    decision: null,
    decision_revision: null,
    decided_by: null,
    decided_at: null,
    note: null,
    evidence_reference: null,
    ...overrides,
  };
}

// ---- 已处理判定（与后端 RESOLVED_OBLIGATION_DECISIONS 逐字一致） -------------

assert.equal(isObligationHandled(item({ decision: "verified_ok" })), true);
assert.equal(isObligationHandled(item({ decision: "verified_issue" })), true);
assert.equal(isObligationHandled(item({ decision: "not_applicable" })), true);
assert.equal(isObligationHandled(item({ decision: "pending" })), false);
assert.equal(isObligationHandled(item({ decision: null })), false);
assert.equal(isObligationHandled(item({ decision: "approved" })), false, "未知值按待处理");
assert.equal(isObligationHandled(null), false);

// ---- 待办数 -------------------------------------------------------------------

const reviewBase: ReviewLifecycleDataRecord = {
  slot_id: "slot-1",
  current_document_version_id: 11,
  current_analysis: {
    job_uuid: "job-1",
    analysis_revision: 1,
    analysis_basis_token: "job-1:1",
    document_version_id: 11,
    status: "done",
    completed: true,
    formal_issue_count: 0,
  },
  current_session: null,
  history: [],
  completion_gate: { can_complete: false, blockers: [] },
};

assert.equal(pendingObligationCount(null), null, "老后端没有这个字段 ⇒ 待办数未知");
assert.equal(
  pendingObligationCount({
    ...reviewBase,
    obligation_review: { available: false, reason: "coverage_unavailable", pending_total: null, items: [] },
  }),
  null,
  "覆盖不可用 ⇒ 待办数为 null，不许显示成 0",
);
assert.equal(
  pendingObligationCount({
    ...reviewBase,
    obligation_review: {
      available: true,
      reason: null,
      pending_total: 1,
      items: [item({ decision: "verified_ok" }), item({ obligation_id: "OBL-B" })],
    },
  }),
  1,
  "服务端给了 pending_total 就用它（权威口径）",
);
assert.equal(
  pendingObligationCount({
    ...reviewBase,
    obligation_review: {
      available: true,
      reason: null,
      pending_total: null,
      items: [item(), item({ obligation_id: "OBL-B", decision: "verified_issue" })],
    },
  }),
  1,
  "缺 pending_total 时按 items 推：2 项里 1 项已处理 ⇒ 1 项待办",
);

// ---- 标签与色调 -----------------------------------------------------------------

assert.equal(obligationDecisionLabel("verified_ok"), "已补核通过");
assert.equal(obligationDecisionLabel("verified_issue"), "已确认存在问题");
assert.equal(obligationDecisionLabel("not_applicable"), "人工判定不适用");
assert.equal(obligationDecisionLabel("pending"), "待处理");
assert.equal(obligationDecisionLabel(null), "待处理");
assert.equal(obligationDecisionLabel("approved"), "approved", "未识别的码不吞、不编");
assert.equal(obligationDecisionTone("verified_ok"), "done");
assert.equal(obligationDecisionTone("verified_issue"), "failed");
assert.equal(obligationDecisionTone("not_applicable"), "neutral");
assert.equal(obligationDecisionTone(null), "lowconf");

// ---- 阻塞文案指路到补核页签（WP3-B 交付后不再说"该能力将在…中处理"） -------------

const label = blockerLabel("blocking_obligations", 2);
assert.ok(label.includes("还有 2 项检查义务"), "计数必须说出来");
assert.ok(label.includes("人工补核"), "必须保留人工补核字样");
assert.ok(label.includes("检查补核"), "必须指路到补核页签");
assert.ok(!label.includes("将在"), "不再说能力『将来才有』");
assert.equal(
  blockerLabel("blocking_obligations", null),
  "存在需要人工补核的检查义务",
);

// ---- 上下文解析对 obligation_review 的容忍 --------------------------------------

const state = toReviewContextState({
  review_context: { available: true, reason: null, slot_id: "slot-1", job_uuid: "job-1" },
  review: {
    ...reviewBase,
    obligation_review: {
      available: true,
      reason: null,
      pending_total: 0,
      items: [item({ decision: "verified_ok" })],
    },
  },
});
assert.equal(state.kind, "available");
if (state.kind === "available") {
  assert.equal(state.review.obligation_review?.pending_total, 0);
}

const legacy = toReviewContextState({
  review_context: { available: true, reason: null, slot_id: "slot-1", job_uuid: "job-1" },
  review: reviewBase,
});
assert.equal(legacy.kind, "available");
if (legacy.kind === "available") {
  assert.equal(legacy.review.obligation_review, undefined, "老后端没有该字段也不影响解析");
}

console.log("reviewObligationAdapters.test.ts passed");
