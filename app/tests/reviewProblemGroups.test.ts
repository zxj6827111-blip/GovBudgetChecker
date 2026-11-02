import assert from "node:assert/strict";

import { toUiProblems } from "../lib/uiAdapters";
import type { JobDetailRecord } from "../lib/uiAdapters";

// 批次1：页面卡片必须与后端规范问题组同源——三组单位缺陷各一张卡片，
// 两条 V33-251 关联症状各一张，CMM-011 不再作为额外待办出现。

const groupBase = {
  source: "rule",
  severity: "error",
  title: "金额单位口径错误",
  message: "原文179.82元折合0.017982万元",
  location: {
    page: 20,
    location_level: "exact_span",
    verification_status: "verified",
    bbox: [1, 2, 3, 4],
    problem_group_id: "pg-aaa",
    defect_type: "amount_unit_missing_wan",
    source_anchor: { physical_page: 20, literal: "179.82元", text_span: [10, 19] },
    related_rule_ids: ["V33-222", "CMM-011"],
  },
  related_rule_ids: ["V33-222", "CMM-011"],
  merged_issue_ids: ["V33-222-6", "CMM-011-7"],
  evidence_status: undefined,
};

const issues = [
  { ...groupBase, id: "V33-222-9", rule_id: "V33-222" },
  { ...groupBase, id: "V33-222-10", rule_id: "V33-222",
    location: { ...groupBase.location, page: 21, problem_group_id: "pg-bbb" } },
  { ...groupBase, id: "V33-222-11", rule_id: "V33-222",
    location: { ...groupBase.location, page: 21, problem_group_id: "pg-ccc" } },
  {
    id: "V33-251-7", source: "rule", rule_id: "V33-251", severity: "warn",
    title: "年初预算合计不一致", message: "相差300.00万元",
    location: { page: 19, location_level: "exact_span", verification_status: "verified" },
  },
  {
    id: "V33-251-8", source: "rule", rule_id: "V33-251", severity: "warn",
    title: "支出决算合计不一致", message: "相差1176.68万元",
    location: { page: 19, location_level: "exact_span", verification_status: "verified",
      related_problem_group_ids: ["pg-aaa", "pg-bbb", "pg-ccc"] },
  },
];

const detail = {
  result: {},
  review_report: { issues, tips: [] },
  ignored_issue_ids: [],
} as unknown as JobDetailRecord;

const problems = toUiProblems(detail);
assert.equal(problems.length, 5, "五个规范问题组/关联症状各一张卡片");
assert.equal(problems.filter((problem) => problem.ruleId === "CMM-011").length, 0,
  "CMM-011 已并入 V33-222，不得成为额外待办");
const grouped = problems.filter((problem) => problem.problemGroupId);
assert.equal(grouped.length, 3);
assert.deepEqual(new Set(grouped.map((problem) => problem.problemGroupId)),
  new Set(["pg-aaa", "pg-bbb", "pg-ccc"]));
for (const problem of grouped) {
  assert.deepEqual(problem.relatedRuleIds, ["V33-222", "CMM-011"]);
  assert.ok(problem.memberIssueIds?.includes("CMM-011-7"));
}
const symptoms = problems.filter((problem) => problem.ruleId === "V33-251");
assert.equal(symptoms.length, 2);
assert.equal(symptoms.filter((problem) => problem.problemGroupId).length, 0);

console.log("reviewProblemGroups.test.ts passed");
