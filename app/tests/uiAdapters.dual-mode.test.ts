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
        severity: "high",
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

const filteredProblems = toUiProblems({
  ...baseDetail,
  ignored_issue_ids: ["ai-2", "rule-1"],
} as any);
assert.deepEqual(
  filteredProblems.map((problem) => problem.id),
  ["ai-1", "ai-3", "ai-4"],
  "ignored issues should still be removed after merged issue projection",
);

console.log("uiAdapters dual-mode tests passed");
