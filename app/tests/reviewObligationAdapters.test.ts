import assert from "node:assert/strict";

import {
  blockerLabel,
  obligationDecisionLabel,
  obligationDecisionTone,
} from "../lib/reviewLifecyclePresentation";
import {
  cascadedChecksByProblemId,
  obligationAutoEvidence,
  obligationItemsByProblemId,
  obligationProblemId,
  obligationStatusEntries,
  readableObligationReason,
  toObligationProblems,
} from "../app/components/review-workbench/obligationProblems";
import { isFormalProblem, problemReviewKind } from "../app/components/review-workbench/reviewWorkbenchAdapters";
import {
  canDecideObligations,
  isObligationHandled,
  obligationEvidencePolicy,
  obligationEvidenceSatisfied,
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

assert.equal(obligationDecisionLabel("verified_ok"), "已核对无误");
assert.equal(obligationDecisionLabel("verified_issue"), "已确认有问题");
assert.equal(obligationDecisionLabel("not_applicable"), "不适用");
assert.equal(obligationDecisionLabel("pending"), "待处理");
assert.equal(obligationDecisionLabel(null), "待处理");
assert.equal(obligationDecisionLabel("approved"), "approved", "未识别的码不吞、不编");
assert.equal(obligationDecisionTone("verified_ok"), "done");
assert.equal(obligationDecisionTone("verified_issue"), "failed");
assert.equal(obligationDecisionTone("not_applicable"), "neutral");
assert.equal(obligationDecisionTone(null), "lowconf");

// ---- 阻塞文案指路到「必须处理」（补核已并入必须处理，不再有单独的补核页签） -------------

const label = blockerLabel("blocking_obligations", 2);
assert.ok(label.includes("还有 2 项"), "计数必须说出来");
assert.ok(label.includes("未能自动核对"), "用页面上同一个说法");
assert.ok(label.includes("必须处理"), "必须指路到处理位置");
assert.ok(!label.includes("检查补核"), "补核页签已取消，不能再指过去");
assert.ok(!label.includes("将在"), "不再说能力『将来才有』");
assert.equal(
  blockerLabel("blocking_obligations", null),
  "还有“未能自动核对”的检查待处理",
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

// ---- 补核动作的前端即时门槛（真正判定在服务端，这只是少让人白点） -------------

assert.equal(canDecideObligations(null), false, "没有复核上下文 ⇒ 不可补核");
assert.equal(canDecideObligations(reviewBase), false, "没有活动会话 ⇒ 不可补核");
assert.equal(
  canDecideObligations({
    ...reviewBase,
    current_session: {
      review_session_id: "sess-1",
      status: "in_progress",
      slot_id: "slot-1",
      document_version_id: 11,
      analysis_job_uuid: "job-1",
      analysis_basis_token: "job-1:1",
      started_by: "r",
      started_at: "2026-09-23T00:00:00+00:00",
      completed_by: null,
      completed_at: null,
      invalidated_at: null,
      invalidated_reason: null,
      review_result: {},
    },
  }),
  true,
);
assert.equal(
  canDecideObligations({
    ...reviewBase,
    current_session: {
      review_session_id: "sess-2",
      status: "completed",
      slot_id: "slot-1",
      document_version_id: 11,
      analysis_job_uuid: "job-1",
      analysis_basis_token: "job-1:1",
      started_by: "r",
      started_at: "2026-09-23T00:00:00+00:00",
      completed_by: "r",
      completed_at: "2026-09-23T01:00:00+00:00",
      invalidated_at: null,
      invalidated_reason: null,
      review_result: {},
    },
  }),
  false,
  "已完成复核 ⇒ 不可再补核（须先重新复核）",
);

// ---- 依据纪律（与服务端 _obligation_evidence_error 逐字对齐） -------------------

// verified_ok：note 或 evidence 至少一项
assert.equal(obligationEvidencePolicy("verified_ok").noteRequired, false);
assert.equal(obligationEvidencePolicy("verified_ok").evidenceAloneSatisfies, true);
assert.equal(obligationEvidenceSatisfied("verified_ok", "", ""), false);
assert.equal(obligationEvidenceSatisfied("verified_ok", "已核对", ""), true);
assert.equal(obligationEvidenceSatisfied("verified_ok", "", "第 12 页"), true);

// verified_issue：note 必填
assert.equal(obligationEvidencePolicy("verified_issue").noteRequired, true);
assert.equal(obligationEvidenceSatisfied("verified_issue", "", "第 3 页"), false);
assert.equal(obligationEvidenceSatisfied("verified_issue", "确认存在差异", ""), true);

// not_applicable：note 必填
assert.equal(obligationEvidencePolicy("not_applicable").noteRequired, true);
assert.equal(obligationEvidenceSatisfied("not_applicable", "", ""), false);
assert.equal(obligationEvidenceSatisfied("not_applicable", "本单位不涉及", ""), true);

// pending：无须依据（置回待处理不产生放行效果）
assert.equal(obligationEvidenceSatisfied("pending", "", ""), true);
// 未知值同样放行客户端提示——服务端会先用 400/422 拒绝它，提示不构成放行
assert.equal(obligationEvidenceSatisfied("approved", "", ""), true);

// ---- 补核并入「必须处理」：条目转换、处理状态、核对无误的自动证据 ----------------------

const mergedBlock = {
  available: true,
  reason: null,
  pending_total: 1,
  items: [
    {
      obligation_id: "OBL-INTERNAL-BASIC-EXPENSE", group_id: "g", group_title: "表内勾稽", title: "基本支出 = 人员经费 + 公用经费",
      status: "insufficient_data", reason: "insufficient_data", reason_label: "取数不足",
      detail: "人员经费合计的决算操作数缺失或不唯一", basis: null,
      locations: [{ page: 12, label: "基本支出决算表", bbox: [10, 20, 300, 40] }, { page: 13, label: null, bbox: null }],
      decision: null, decision_revision: null, decided_by: null, decided_at: null, note: null, evidence_reference: null,
    },
    {
      obligation_id: "OBL-TXT-T5", group_id: "g", group_title: "说明一致性", title: "一般公共预算支出决算表 与 功能分类说明一致",
      status: "insufficient_data", reason: "insufficient_data", reason_label: "取数不足",
      detail: "说明中的项“其他城乡社区支出”在表内找不到同名科目", basis: null, locations: [],
      decision: "verified_issue", decision_revision: 2, decided_by: "reviewer", decided_at: "2026-11-01", note: "科目名称写错", evidence_reference: null,
    },
  ],
};
const mergedProblems = toObligationProblems(mergedBlock, "job-merge");
assert.equal(mergedProblems.length, 2, "已处理的补核也留在队列里，便于查看与恢复待处理");
const [basicExpense, t5] = mergedProblems;
assert.equal(basicExpense.id, obligationProblemId("OBL-INTERNAL-BASIC-EXPENSE"));
assert.equal(basicExpense.title, "基本支出 = 人员经费 + 公用经费", "列表里由分组和徽标说明“未能自动核对”，标题不再重复");
assert.equal(basicExpense.description, "取数不足：人员经费合计的决算数值没取到（或取到不止一个）");
assert.equal(basicExpense.page, 12);
assert.deepEqual(basicExpense.bbox, [10, 20, 300, 40]);
assert.equal(basicExpense.evidenceRefs?.length, 2);
assert.equal(basicExpense.evidenceRefs?.[1].locationLevel, "page", "没有框的位置只翻页，不画框");
assert.equal(isFormalProblem(basicExpense), true, "补核计入必须处理");
assert.equal(problemReviewKind(basicExpense), "obligation");
assert.equal(t5.page, undefined, "没有位置时不编页码");
assert.equal(t5.status, "resolved");
assert.deepEqual(obligationStatusEntries(mergedBlock), { [obligationProblemId("OBL-TXT-T5")]: "verified_issue" });
assert.equal(obligationItemsByProblemId(mergedBlock)[t5.id].decision_revision, 2, "提交时要带回乐观锁版本");
assert.deepEqual(toObligationProblems({ ...mergedBlock, available: false }), [], "补核块不可用时不出条目");
assert.deepEqual(toObligationProblems(null), []);
// 核对无误一键提交：证据位置取系统定位并注明来源；没有定位时返回 null，由人写一句。
assert.equal(obligationAutoEvidence(mergedBlock.items[0]), "第 12 页 · 基本支出决算表；第 13 页（按系统定位核对）");
assert.equal(obligationAutoEvidence(mergedBlock.items[1]), null);
const manyPlaces = { ...mergedBlock.items[0], locations: Array.from({ length: 40 }, (_, index) => ({ page: index + 1, label: "很长的表名用于测试截断", bbox: null })) };
assert.ok((obligationAutoEvidence(manyPlaces) ?? "").length <= 200, "不超过后端证据位置上限");

// 引擎卡点里的机器字段名只在展示时换成人话；普通缩写不动。
assert.equal(
  readableObligationReason("人员经费合计的total_actual/决算操作数缺失或不唯一；公用经费合计的total_actual/决算操作数缺失或不唯一"),
  "人员经费合计的决算数值没取到（或取到不止一个）；公用经费合计的决算数值没取到（或取到不止一个）",
);
assert.equal(readableObligationReason("PDF/表8 未识别到"), "PDF/表8 未识别到");
assert.equal(readableObligationReason(null), "");

// IR09 跨层往返：后端 related 关联框必须成为可选证据引用（bbox 保留、
// search_region/unverified 诚实粒度），预览按选中 key 取框即可画到。
{
  const item = {
    obligation_id: "OBL-TEST-RELATED",
    group_id: null,
    group_title: null,
    title: "测试义务",
    status: "insufficient_data",
    reason: "insufficient_data",
    reason_label: "取数不足",
    detail: "",
    decision: null,
    decision_revision: null,
    decided_by: null,
    decided_at: null,
    note: null,
    evidence_reference: null,
    locations: [
      { page: 15, label: "三公经费表", bbox: [10, 20, 30, 40],
        related: [{ page: 15, label: "三公经费说明", bbox: [50, 60, 70, 80] }] },
    ],
  };
  const problems = toObligationProblems({
    available: true, reason: null, pending_total: 1,
    items: [item],
  }, "job-related");
  const refs = problems[0]?.evidenceRefs ?? [];
  assert.equal(refs.length, 2, "主框+关联框都成为可选证据引用");
  assert.deepEqual(refs[0].bbox, [10, 20, 30, 40]);
  assert.deepEqual(refs[1].bbox, [50, 60, 70, 80], "第二个框不再丢失");
  assert.equal(refs[1].locationLevel, "search_region", "关联框同样是诚实粒度");
  assert.equal(refs[1].verificationStatus, "unverified");
  assert.ok(refs[1].key.startsWith(`${refs[0].key}|r1`), "关联框 key 可追溯到主框");
}

{
  // 缺表/空表连带项（2026-11-08）：按根问题 id 分组，不进待办、不生成「未能自动核对」条目。
  const block = {
    available: true, reason: null, pending_total: 0, items: [],
    cascaded: [
      { root_issue_id: "f-missing", items: [
        { obligation_id: "OBL-INTERNAL-T3-TOTAL", title: "支出决算表：本年支出合计 = 基本支出 + 项目支出" },
        { obligation_id: "OBL-CROSS-T1-T3", title: "收入支出决算总表 与 支出决算表 同口径总额一致" },
      ] },
      { root_issue_id: "f-empty", items: [] },
    ],
  };
  const grouped = cascadedChecksByProblemId(block);
  assert.deepEqual(Object.keys(grouped), ["f-missing"], "空分组不挂");
  assert.equal(grouped["f-missing"].length, 2);
  assert.equal(toObligationProblems(block, "job").length, 0, "连带项不进入必须处理");
  assert.deepEqual(cascadedChecksByProblemId({ ...block, available: false }), {});
  assert.deepEqual(cascadedChecksByProblemId({ available: true, reason: null, pending_total: 0, items: [] }), {},
    "旧后端没有 cascaded 字段时为空");
}

console.log("reviewObligationAdapters.test.ts passed");
