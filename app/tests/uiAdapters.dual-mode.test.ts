import assert from "node:assert/strict";

import { toUiProblems } from "../lib/uiAdapters";

const baseDetail = {
  job_id: "job-dual-mode-count-001",
  result: {
    ai_findings: [
      {
        id: "ai-1",
        source: "ai",
        rule_id: "AI-1",
        severity: "critical",
        title: "AI issue 1",
        message: "AI issue 1 detail",
        evidence: [{ page: 1, text: "alpha" }],
        location: { page: 1 },
      },
      {
        id: "ai-2",
        source: "ai",
        rule_id: "AI-2",
        severity: "high",
        title: "AI issue 2",
        message: "AI issue 2 detail",
        evidence: [{ page: 2, text: "beta" }],
        location: { page: 2 },
      },
      {
        id: "ai-3",
        source: "ai",
        rule_id: "AI-3",
        severity: "medium",
        title: "AI issue 3",
        message: "AI issue 3 detail",
        evidence: [{ page: 3, text: "gamma" }],
        location: { page: 3 },
      },
      {
        id: "ai-4",
        source: "ai",
        rule_id: "AI-4",
        severity: "low",
        title: "AI issue 4",
        message: "AI issue 4 detail",
        evidence: [{ page: 4, text: "delta" }],
        location: { page: 4 },
      },
    ],
    rule_findings: [
      {
        id: "rule-1",
        source: "rule",
        rule_id: "RULE-1",
        severity: "medium",
        title: "Rule issue 1",
        message: "Rule issue 1 detail",
        evidence: [{ page: 5, text: "epsilon" }],
        location: { page: 5 },
      },
    ],
    merged: {
      totals: {
        ai: 4,
        rule: 1,
        merged: 5,
        conflicts: 0,
        agreements: 0,
      },
      merged_ids: ["ai-1", "ai-2", "ai-3", "ai-4", "rule-1"],
      conflicts: [],
      agreements: [],
    },
  },
};

const problems = toUiProblems(baseDetail as any);
assert.equal(problems.length, 5, "dual-mode payload should expose merged issue count in detail view");
assert.deepEqual(
  problems.map((problem) => problem.id),
  ["ai-1", "ai-2", "ai-3", "ai-4", "rule-1"],
  "merged_ids should drive the detail list order",
);
assert.deepEqual(
  problems.map((problem) => problem.severity),
  ["critical", "high", "medium", "low", "medium"],
  "ui adapter should preserve backend five-level severity",
);
assert.deepEqual(
  problems.map((problem) => problem.severityLabel),
  ["严重", "高", "中", "低", "中"],
  "ui adapter should expose Chinese severity labels",
);

const filteredProblems = toUiProblems({
  ...baseDetail,
  ignored_issue_ids: ["ai-2", "rule-1"],
} as any);
assert.deepEqual(
  filteredProblems.map((problem) => problem.id),
  ["ai-1", "ai-3", "ai-4"],
  "ignored issues should still be removed after merged issue projection",
);

const nameAlignmentProblems = toUiProblems({
  job_id: "job-name-alignment-001",
  result: {
    rule_findings: [
      {
        id: "bud-109-1",
        source: "rule",
        rule_id: "BUD-109",
        severity: "medium",
        message: "预算编制说明类级科目名称与T5不一致",
        suggestion: "请以 T5 表格名称为准修订预算编制说明。",
        display: {
          summary: "预算编制说明功能分类类款项名称与T5不一致",
        },
        location: {
          page: 9,
          expected_name: "卫生健康支出",
          actual_name: "医疗卫生与计划生育支出",
          code_level: "类",
          source_of_truth: "BUD_T5",
        },
        evidence: [{ page: 9, text: "卫生健康支出 / 医疗卫生与计划生育支出" }],
      },
    ],
  },
} as any);
assert.equal(nameAlignmentProblems.length, 1, "name alignment issue should be projected");
assert.equal(
  nameAlignmentProblems[0].title,
  "预算编制说明功能分类类款项名称与T5不一致",
  "display.summary should override generic message in problem title",
);
assert.equal(
  nameAlignmentProblems[0].category,
  "类款项口径一致性",
  "BUD-109 should land in the dedicated name-alignment category",
);
assert.equal(
  nameAlignmentProblems[0].suggestion,
  "请以 T5 表格名称为准修订预算编制说明。",
  "backend suggestion should be preserved",
);
assert.equal(nameAlignmentProblems[0].expectedName, "卫生健康支出");
assert.equal(nameAlignmentProblems[0].actualName, "医疗卫生与计划生育支出");
assert.equal(nameAlignmentProblems[0].codeLevel, "类");
assert.equal(nameAlignmentProblems[0].sourceOfTruth, "BUD_T5");

console.log("uiAdapters dual-mode tests passed");
