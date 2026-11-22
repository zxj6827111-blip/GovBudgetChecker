import assert from "node:assert/strict";

import { selectCorrectionTargets } from "../lib/textCorrectionTargets";

const first = { target_id: "t-first", issue_ids: ["issue-1"], page: 26, rule_id: "CMM-011" };
const second = { target_id: "t-second", issue_ids: ["issue-2"], page: 26, rule_id: "CMM-011" };

// 点击第二笔：必须选中第二条的 target_id，不能被“同页第一条”抢先。
{
  const selection = selectCorrectionTargets([first, second], {
    id: "issue-2", page: 26, ruleId: "CMM-011",
  });
  assert.deepEqual(selection.pool.map((item) => item.target_id), ["t-second"]);
  assert.equal(selection.initialTargetId, "t-second");
  assert.equal(selection.ambiguous, false);
}

// 交换列表顺序后仍按唯一问题 ID 命中。
{
  const selection = selectCorrectionTargets([second, first], {
    id: "issue-1", page: 26, ruleId: "CMM-011",
  });
  assert.deepEqual(selection.pool.map((item) => item.target_id), ["t-first"]);
  assert.equal(selection.initialTargetId, "t-first");
}

// 没有唯一 ID 绑定时列出同页候选，且不预选任何一条。
{
  const selection = selectCorrectionTargets([first, second], {
    id: "unknown-issue", page: 26, ruleId: "CMM-011",
  });
  assert.deepEqual(selection.pool.map((item) => item.target_id), ["t-first", "t-second"]);
  assert.equal(selection.initialTargetId, null);
  assert.equal(selection.ambiguous, true);
}

// 只有一条同页候选时可以回退选中。
{
  const selection = selectCorrectionTargets([first], {
    id: "unknown-issue", page: 26, ruleId: "CMM-011",
  });
  assert.equal(selection.initialTargetId, "t-first");
  assert.equal(selection.ambiguous, false);
}
