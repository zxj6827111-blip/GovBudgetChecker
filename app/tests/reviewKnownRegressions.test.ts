import assert from "node:assert/strict";

import { getDisplayIssueTotal, toUiProblems } from "../lib/uiAdapters";
import { resolveIssueCountText } from "../app/components/workspace/workbenchAdapters";
import { evidenceExcerptLabel } from "../lib/evidenceIdentity";
import { countFormalProblems, problemReviewKind, computeWorkflowStatusCounts } from "../app/components/review-workbench/reviewWorkbenchAdapters";

const issue = (id: string, severity: string, verified = false) => ({
  id, severity, source: "rule", title: id,
  ...(verified ? { evidence_status: "complete", evidence_verification: { status: "verified", source_identity_complete: true } } : {}),
});
const detail = {
  job_id: "counts",
  review_report: {
    issues: [issue("a", "high", true), issue("b", "medium", true), issue("c", "medium", true),
             issue("d", "manual_review"), issue("e", "manual_review"), issue("f", "manual_review")],
    tips: [issue("g", "info"), issue("h", "info"), issue("i", "info")],
    manual_review: [0, 1, 2].map((code) => ({ record_type: "review_reason", code: String(code) })),
  },
};
const problems = toUiProblems(detail, { includeEvidencePreview: false });
assert.equal(problems.length, 12);
assert.equal(problems.filter((p) => problemReviewKind(p) === "issue").length, 3);
assert.equal(problems.filter((p) => problemReviewKind(p) === "manual_review").length, 3);
assert.equal(problems.filter((p) => problemReviewKind(p) === "tip").length, 3);
assert.equal(problems.filter((p) => problemReviewKind(p) === "review_notice").length, 3);
assert.equal(getDisplayIssueTotal({ review_issue_total: 3, merged_issue_total: 9, review_item_count: 5 }), 3);
assert.equal(resolveIssueCountText({ job_id: "counts", review_issue_total: 3, merged_issue_total: 9, review_item_count: 5 }), "3");
assert.equal(getDisplayIssueTotal({ review_issue_total: 0, review_candidate_total: 6, merged_issue_total: 0 }), 6);
assert.equal(resolveIssueCountText({ job_id: "candidate-counts", review_issue_total: 0, review_candidate_total: 6 }), "6");
assert.equal(resolveIssueCountText({ job_id: "unknown" }), "—");
assert.equal(resolveIssueCountText({ job_id: "zero", review_issue_total: 0 }), "0");
assert.equal(countFormalProblems(problems), 3);
assert.equal(computeWorkflowStatusCounts(problems, {}).pending, 3);
const filtered = toUiProblems({ ...detail, ignored_issue_ids: ["a"] }, { includeEvidencePreview: false });
assert.equal(filtered.filter((p) => problemReviewKind(p) === "issue").length, 2);
const sourceDetail = (evidenceStatus: string) => ({
  job_id: "source", review_report: {
    issues: [{ ...issue("verified", "high"), evidence_status: evidenceStatus,
      evidence_verification: { status: "verified", source_identity_complete: true },
      evidence: [{ page: 25, text: "年初预算为24.00万元，支出决算为22.17万元。" }],
    }],
  },
});
const verified = toUiProblems(sourceDetail("complete"), { includeEvidencePreview: false })[0];
assert.equal(verified.sourceIdentityVerified, true);
assert.equal(evidenceExcerptLabel(verified.sourceIdentityVerified), "原文证据");
assert.equal(toUiProblems(sourceDetail("degraded_to_manual_review"))[0].sourceIdentityVerified, false);
console.log("reviewKnownRegressions.test.ts passed");
