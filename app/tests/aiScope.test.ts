import assert from "node:assert/strict";

import { deriveAiScope, formatPageList } from "../lib/aiScope";

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
