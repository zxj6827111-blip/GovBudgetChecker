import assert from "node:assert/strict";

import { deriveAiScope, formatPageList, groupFailedUnits } from "../lib/aiScope";

// 完整覆盖：只有 ok/empty 才算有效完成。
const complete = deriveAiScope({
  ai_execution: { requested: true, state: "succeeded" },
  ai_coverage: {
    status: "complete",
    planned_calls: 4,
    completed_calls: 4,
    succeeded_calls: 4,
    failed_calls: 0,
  },
  conclusion_scope: "no_findings_within_covered_checks",
});
assert.equal(complete.tone, "success");
assert.match(complete.headline, /已完整完成（4\/4）/);
assert.match(complete.conclusionScopeLabel, /未发现问题仅限/);
// 没记录专项与文本段时不猜单元构成。
assert.equal(complete.unitBreakdown, "");

// 检查单元 = 专项 × 文本段：4/4 是调用分段，不是问题条数。
const withWindows = deriveAiScope({
  ai_execution: { requested: true, state: "succeeded" },
  ai_coverage: {
    status: "complete",
    planned_calls: 4,
    succeeded_calls: 4,
    failed_calls: 0,
    specialties: ["text_semantics", "narrative_consistency"],
    windows: [{ window_id: 0, pages: [1, 2] }, { window_id: 1, pages: [2, 3] }],
  },
});
assert.equal(withWindows.unitBreakdown, "2 个专项 × 2 段文本");
// 乘积与计划数对不上时不写构成。
assert.equal(deriveAiScope({
  ai_coverage: { status: "complete", planned_calls: 3, succeeded_calls: 3, failed_calls: 0, specialties: ["a", "b"], windows: [{}, {}] },
}).unitBreakdown, "");

// 部分失败：4 个单元、3 个成功、1 个失败 → 不能说“已处理 4/4”。
const partial = deriveAiScope({
  ai_execution: { requested: true, state: "degraded", accepted_candidates: 3 },
  ai_coverage: {
    status: "partial",
    planned_calls: 4,
    completed_calls: 4,
    succeeded_calls: 3,
    failed_calls: 1,
    retried_calls: 1,
    failed_units: [{
      specialty: "narrative_consistency",
      status: "error",
      pages: [13, 14, 20],
      error_type: "server_error",
      http_status: 524,
      attempts: 2,
      retryable: true,
    }],
  },
  conclusion_scope: "incomplete_scope",
});
assert.equal(partial.tone, "warning");
assert.match(partial.headline, /有效完成 3\/4/);
assert.match(partial.headline, /失败 1/);
assert.match(partial.headline, /另有 1 个单元重试/);
assert.equal(partial.failedUnits.length, 1);
assert.equal(partial.failedUnits[0].specialtyLabel, "说明一致性");
assert.equal(partial.failedUnits[0].pagesLabel, "第13-14、20页");
assert.equal(partial.failedUnits[0].errorType, "server_error");
assert.equal(partial.failedUnits[0].httpStatus, 524);
assert.equal(partial.failedUnits[0].attempts, 2);
assert.equal(partial.needsRecheck, true);
assert.match(partial.conclusionScopeLabel, /不能得出完整结论/);

// 旧记录只有 completed_calls + failed_calls：反推有效完成，不能把“已结束”当“完成”。
const legacyCounts = deriveAiScope({
  ai_execution: { requested: true, state: "degraded" },
  ai_coverage: { status: "partial", planned_calls: 4, completed_calls: 4, failed_calls: 1 },
});
assert.match(legacyCounts.headline, /有效完成 3\/4/);

// 有完成状态但没有覆盖记录：不能写“覆盖完整”，必须提示“未记录”。
const missingCoverage = deriveAiScope({
  ai_execution: { requested: true, state: "succeeded" },
  conclusion_scope: "no_findings_within_covered_checks",
});
assert.equal(missingCoverage.missingCoverage, true);
assert.match(missingCoverage.headline, /覆盖信息未记录/);
assert.doesNotMatch(missingCoverage.headline, /已完整完成/);
assert.equal(missingCoverage.needsRecheck, true);

// 纯规则任务：直接写“文字语义未执行”。
const ruleOnly = deriveAiScope(
  { ai_execution: { requested: false, state: "not_requested" } },
  { requestedHint: false },
);
assert.equal(ruleOnly.requested, false);
assert.match(ruleOnly.headline, /本次未执行/);
assert.match(ruleOnly.scopeLine, /未执行/);

// 完全没有记录：不默认已完成。
const unknown = deriveAiScope({});
assert.match(unknown.headline, /未记录/);
assert.match(unknown.scopeLine, /不能据此认为已完成/);

// 单行状态条：同专项的重叠窗口合并成一段页码，结论作用域用短语。
const overlapping = deriveAiScope({
  ai_execution: { requested: true, state: "degraded" },
  ai_coverage: {
    status: "partial",
    planned_calls: 4,
    succeeded_calls: 2,
    failed_calls: 2,
    failed_units: [
      { specialty: "text_semantics", status: "invalid", pages: [1, 2, 3, 21, 22, 23], error_type: "response_invalid" },
      { specialty: "text_semantics", status: "invalid", pages: [21, 22, 23, 24, 25, 26, 27], error_type: "response_invalid" },
    ],
  },
  conclusion_scope: "incomplete_scope",
});
assert.equal(overlapping.failedUnits.length, 2);
assert.equal(overlapping.failedGroups.length, 1);
assert.equal(overlapping.failedGroups[0].specialtyLabel, "文字语义");
assert.equal(overlapping.failedGroups[0].pagesLabel, "第1-3、21-27页");
assert.equal(overlapping.failedGroups[0].unitCount, 2);
assert.deepEqual(overlapping.failedGroups[0].statusLabels, ["响应不合法"]);
assert.equal(overlapping.brief, "");
assert.equal(overlapping.conclusionScopeShort, "不能得出完整结论");
// 三句不再重复“不能得出完整结论”。
assert.doesNotMatch(overlapping.summary, /完整结论/);

const mixedGroups = groupFailedUnits([
  ...partial.failedUnits,
  { ...partial.failedUnits[0], specialty: "text_semantics", specialtyLabel: "文字语义", pages: [1, 2] },
  { ...partial.failedUnits[0], pages: [15], status: "invalid", statusLabel: "响应不合法" },
]);
assert.deepEqual(mixedGroups.map((group) => `${group.specialtyLabel}${group.pagesLabel}`), [
  "说明一致性第13-15、20页",
  "文字语义第1-2页",
]);
assert.deepEqual(mixedGroups[0].statusLabels, ["调用失败", "响应不合法"]);

assert.equal(ruleOnly.brief, "仅有本地规则结果，文字语义检查未执行");
assert.equal(complete.brief, "");
assert.equal(complete.conclusionScopeShort, "未发现问题仅限已完成范围");
assert.equal(unknown.conclusionScopeShort, "历史结果未记录结论作用域");

// AI 全部完成而整体仍未完成：短语指向补核事项，不说成 AI 未完成。
const aiDoneObligationsPending = deriveAiScope({
  ai_execution: { requested: true, state: "succeeded" },
  ai_coverage: { status: "complete", planned_calls: 2, succeeded_calls: 2, failed_calls: 0 },
  conclusion_scope: "incomplete_scope",
});
assert.equal(aiDoneObligationsPending.tone, "success");
assert.equal(aiDoneObligationsPending.conclusionScopeShort, "仍有未能自动核对的检查待处理");

// 未覆盖页进入短句，避免单行状态条漏掉页码。
const uncovered = deriveAiScope({
  ai_execution: { requested: true, state: "degraded" },
  ai_coverage: {
    status: "partial", planned_calls: 3, succeeded_calls: 3, failed_calls: 0,
    uncovered_ranges: [{ pages: [5, 6] }],
  },
});
assert.match(uncovered.brief, /未覆盖第5-6页/);

// 页码压缩与去重。
assert.equal(formatPageList([20, 13, 14, 13]), "第13-14、20页");
assert.equal(formatPageList([]), "页码未记录");

console.log("aiScope.test.ts passed");

// 结论作用域来自旧结构 quality_gate 或任务顶层时也要取到，不能误报“历史未记录”。
const conclusionFromGate = deriveAiScope({
  ai_execution: { requested: true, state: "degraded" },
  ai_coverage: { status: "partial", planned_calls: 4, succeeded_calls: 3, failed_calls: 1 },
  quality_gate: { conclusion_scope: "incomplete_scope" },
});
assert.match(conclusionFromGate.conclusionScopeLabel, /不能得出完整结论/);

const conclusionFromTop = deriveAiScope(
  { ai_execution: { requested: true, state: "succeeded" }, ai_coverage: { status: "complete", planned_calls: 1, succeeded_calls: 1 } },
  { conclusionScopeHint: "no_findings_within_covered_checks" },
);
assert.match(conclusionFromTop.conclusionScopeLabel, /未发现问题仅限/);

// 完整覆盖但有补试：必须保留“补试成功”的事实，不能写成零重试。
const completeWithRetry = deriveAiScope({
  ai_execution: { requested: true, state: "succeeded" },
  ai_coverage: {
    status: "complete", planned_calls: 4, succeeded_calls: 4, failed_calls: 0, retried_calls: 1,
  },
});
assert.match(completeWithRetry.summary, /1 个单元首次失败后补试成功/);
