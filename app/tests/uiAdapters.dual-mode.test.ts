import assert from "node:assert/strict";

import { getDisplayIssueTotal, toUiProblems } from "../lib/uiAdapters";

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
      {
        id: "ai-5",
        source: "ai",
        rule_id: "AI-5",
        severity: "manual_review",
        severity_label: "待人工复核",
        title: "AI issue 5",
        message: "AI issue 5 detail",
        evidence: [{ page: 6, text: "zeta" }],
        location: { page: 6 },
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
        merged: 6,
        conflicts: 0,
        agreements: 0,
      },
      merged_ids: ["ai-1", "ai-2", "ai-3", "ai-4", "ai-5", "rule-1"],
      conflicts: [],
      agreements: [],
    },
  },
};

const problems = toUiProblems(baseDetail as any);
assert.equal(problems.length, 6, "dual-mode payload should expose merged issue count in detail view");
assert.deepEqual(
  problems.map((problem) => problem.id),
  ["ai-1", "ai-2", "ai-3", "ai-4", "ai-5", "rule-1"],
  "merged_ids should drive the detail list order",
);
assert.deepEqual(
  problems.map((problem) => problem.severity),
  ["critical", "high", "medium", "low", "manual_review", "medium"],
  "ui adapter should preserve backend severity levels including manual_review",
);
assert.deepEqual(
  problems.map((problem) => problem.severityLabel),
  ["严重", "高", "中", "低", "待人工复核", "中"],
  "ui adapter should expose Chinese severity labels",
);

const filteredProblems = toUiProblems({
  ...baseDetail,
  ignored_issue_ids: ["ai-2", "rule-1"],
} as any);
assert.deepEqual(
  filteredProblems.map((problem) => problem.id),
  ["ai-1", "ai-3", "ai-4", "ai-5"],
  "ignored issues should still be removed after merged issue projection",
);

const relationV2Candidates = toUiProblems({
  job_id: "job-relation-v2-candidate",
  result: {
    ai_findings: [baseDetail.result.ai_findings[0]],
    rule_findings: [],
    merged: {
      totals: { ai: 1, rule: 0, merged: 0, conflicts: 0, agreements: 0 },
      merge_policy_version: "fact-check-relation-v1",
      merged_ids: [],
      candidate_issue_ids: ["ai-1"],
      manual_review_issue_ids: [],
    },
  },
  review_report: {
    issues: [],
    tips: [],
    manual_review: [
      {
        record_type: "ai_candidate",
        id: "ai-1",
        title: "AI issue 1",
        message: "等待业务确认的模型候选",
        finding: {
          ...baseDetail.result.ai_findings[0],
          location: { page: 1 },
          evidence: [{ page: 1, text: "候选引用原文" }],
        },
      },
    ],
  },
} as any);
assert.equal(relationV2Candidates.length, 1);
assert.equal(relationV2Candidates[0].id, "ai-1");
assert.equal(
  relationV2Candidates[0].severity,
  "manual_review",
  "AI-only findings must be rendered in the manual-review bucket, not as formal issues",
);
assert.equal(relationV2Candidates[0].snippet, "候选引用原文");

const mergeConflict = toUiProblems({
  job_id: "job-relation-v2-conflict",
  result: { ai_findings: [], rule_findings: [], merged: { merge_policy_version: "fact-check-relation-v1", merged_ids: [] } },
  review_report: {
    issues: [],
    tips: [],
    manual_review: [
      {
        record_type: "merge_conflict",
        id: "merge-conflict:rel-1",
        title: "AI 与规则结果存在冲突",
        message: "双方引用不同原始证据，需复核。",
        ai_finding: {
          id: "ai-side",
          location: { page: 2 },
          evidence: [{ page: 2, text: "AI 引用", bbox: [1, 2, 3, 4], location_level: "exact_span", verification_status: "verified" }],
        },
        rule_finding: {
          id: "rule-side",
          location: { page: 3 },
          evidence: [{ page: 3, text: "规则引用", bbox: [5, 6, 7, 8], location_level: "exact_cell", verification_status: "verified" }],
        },
      },
    ],
  },
} as any);
assert.equal(mergeConflict.length, 1);
assert.deepEqual(
  mergeConflict[0].evidenceRefs.map((ref) => ref.page),
  [2, 3],
  "merge conflict should preserve both source page references for review",
);
assert.deepEqual(
  mergeConflict[0].evidenceRefs.map((ref) => ref.bbox),
  [[1, 2, 3, 4], [5, 6, 7, 8]],
  "only source-provided verified exact evidence boxes may be carried through",
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

const structuredReviewProblems = toUiProblems({
  job_id: "job-structured-review-001",
  result: {
    rule_findings: [
      {
        id: "rule-1",
        source: "rule",
        rule_id: "FIN-001",
        severity: "medium",
        title: "规则问题",
        message: "规则问题详情",
      },
    ],
  },
  structured_ingest: {
    review_item_count: 2,
    review_items: [
      {
        id: "review-low-confidence",
        type: "low_confidence_table",
        severity: "manual_review",
        table_code: "FIN_07_three_public",
        page_number: 29,
        message: "三公经费表识别置信度较低",
        recommended_action: "核对第 29 页表格。",
      },
      {
        id: "review-missing-table",
        type: "missing_core_table",
        severity: "medium",
        table_code: "FIN_02_income",
        message: "未识别到收入决算表",
      },
    ],
  },
} as any);
assert.equal(structuredReviewProblems.length, 3, "structured review items must appear beside rule findings");
assert.deepEqual(
  structuredReviewProblems.slice(1).map((problem) => problem.id),
  ["review-low-confidence", "review-missing-table"],
  "structured review identifiers should remain stable for workflow actions",
);
assert.deepEqual(
  structuredReviewProblems.slice(1).map((problem) => problem.category),
  ["结构化识别复核", "结构化识别复核"],
);
assert.equal(structuredReviewProblems[1].page, 29);
assert.equal(structuredReviewProblems[1].severity, "manual_review");
assert.equal(
  getDisplayIssueTotal({ merged_issue_total: 20, issue_total: 20, review_item_count: 4 }),
  24,
  "task totals must include structured review items instead of hiding them",
);

const reportIssues = toUiProblems({
  job_id: "job-review-report-004",
  result: {
    issues: {
      all: [{ id: "legacy-issue", title: "不得作为当前报告来源", message: "legacy" }],
    },
  },
  review_report: {
    schema_version: 2,
    issues: [
      {
        id: "report-issue",
        source: "rule",
        rule_id: "C-001",
        severity: "high",
        title: "跨表问题",
        message: "分项需要人工确认",
        location: {
          page: 2,
          table_refs: [
            {
              role: "说明",
              page: 2,
              section: "三公经费情况说明",
              location_level: "exact_span",
              verification_status: "verified",
              bbox: [10, 20, 100, 40],
            },
            {
              role: "预算表",
              page: 7,
              table: "三公经费预算表",
              location_level: "region",
              verification_status: "unverified",
              bbox: [11, 21, 101, 41],
            },
          ],
        },
        evidence: [{ page: 2, text: "三公经费合计" }],
      },
    ],
  },
} as any);
assert.deepEqual(
  reportIssues.map((problem) => problem.id),
  ["report-issue"],
  "the canonical ReviewReport must override legacy finding projections",
);
assert.deepEqual(reportIssues[0].evidenceRefs?.map((ref) => ref.page), [2, 7]);
assert.deepEqual(reportIssues[0].evidenceRefs?.[0].bbox, [10, 20, 100, 40]);
assert.equal(reportIssues[0].evidenceRefs?.[1].bbox, undefined, "unverified evidence must not produce a UI highlight");

console.log("uiAdapters dual-mode tests passed");
