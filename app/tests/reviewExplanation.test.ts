import assert from "node:assert/strict";

import { toUiProblems } from "../lib/uiAdapters";

// 与真实 S1 落库结果同形：解释字段在 finding 顶层，merged 只给 id 列表。
const issue = {
  id: "issue-v33-222",
  rule_id: "V33-222",
  source: "rule",
  severity: "high",
  title: "机关事业单位基本养老保险缴费支出表文金额不一致",
  message: "表文金额不一致",
  expected: "165.80",
  actual: "141.12",
  difference: "-24.68",
  check: "final_disclosure_amount",
  table_or_section: "五、一般公共预算财政拨款支出决算情况说明",
  amounts_provenance: "program_calculated",
  calculation_basis: { unit: "万元", precision: 2, rule_version: "v3_3", verified: true },
  location: { page: 20 },
  evidence: [{ page: 20, text: "年初预算为165.80万元，支出决算为141.12万元" }],
  evidence_verification: { status: "verified", source_identity_complete: true },
};

const detail: any = {
  job_id: "job-explanation",
  result: {
    merged: {
      merge_policy_version: "fact-check-relation-v1",
      merged_ids: [issue.id],
    },
    rule_findings: [issue],
    ai_findings: [],
  },
  review_report: undefined,
  structured_ingest: {},
  ignored_issue_ids: [],
};

const problems = toUiProblems(detail);
assert.equal(problems.length, 1);
const mapped = problems[0];
assert.equal(mapped.id, issue.id);
assert.equal(mapped.expected, "165.80");
assert.equal(mapped.actual, "141.12");
assert.equal(mapped.difference, "-24.68");
assert.equal(mapped.check, "final_disclosure_amount");
assert.equal(mapped.tableOrSection, "五、一般公共预算财政拨款支出决算情况说明");
assert.equal(mapped.amountsProvenance, "program_calculated");
assert.deepEqual(mapped.calculationBasis, issue.calculation_basis);

// 历史问题没有这些字段时必须保持 undefined，不能补成 0 或空字符串冒充已解释。
const oldDetail: any = {
  job_id: "job-old",
  result: {
    merged: { merge_policy_version: "fact-check-relation-v1", merged_ids: ["old-1"] },
    rule_findings: [{
      id: "old-1", rule_id: "OLD-1", source: "rule", severity: "low",
      title: "旧结果", message: "旧结果", location: { page: 1 },
    }],
    ai_findings: [],
  },
  structured_ingest: {},
  ignored_issue_ids: [],
};
const oldMapped = toUiProblems(oldDetail)[0];
assert.equal(oldMapped.expected, undefined);
assert.equal(oldMapped.actual, undefined);
assert.equal(oldMapped.difference, undefined);
assert.equal(oldMapped.amountsProvenance, undefined);

console.log("reviewExplanation.test.ts passed");

// 复核报告里的 AI 候选：字段在 manual_review[].finding 里，适配器重建对象时
// 必须把解释字段一起带出来，否则页面只剩 message，解释块和“模型声称”提示都会消失。
const aiFinding = {
  id: "ai_semantic_canonical_1",
  rule_id: "AI-XSEC-005",
  source: "ai",
  title: "跨段同口径表述矛盾",
  message: "跨段同口径表述矛盾",
  expected: "表格列示该科目支出决算合计为165.80万元，待核实。",
  actual: "说明列示该科目支出决算为141.12万元，待核实。",
  difference: null,
  check: "同年度同科目一般公共预算财政拨款支出表与说明一致性",
  table_or_section: "五（三）第3项：机关事业单位基本养老保险缴费支出",
  amounts_provenance: "model_claimed_not_verified",
  calculation_basis: { source: "model_claim", verified: false, unit: null },
  location: {
    page: 20,
    evidence_pairs: [
      { page: 13, quote: "165.80", table_or_section: "一般公共预算财政拨款支出决算表" },
      { page: 20, quote: "141.12", table_or_section: "五（三）第3项" },
    ],
  },
  evidence: [{ page: 20, text: "支出决算为141.12万元" }],
  evidence_status: "complete",
  evidence_verification: { status: "verified", source_identity_complete: true },
};
const canonicalDetail: any = {
  job_id: "job-canonical-ai",
  result: { ai_findings: [aiFinding], rule_findings: [] },
  review_report: {
    issues: [],
    tips: [],
    manual_review: [{ id: "ai_semantic_canonical_1", record_type: "ai_candidate", finding: aiFinding }],
  },
  structured_ingest: {},
  ignored_issue_ids: [],
};
const canonicalMapped = toUiProblems(canonicalDetail)[0];
assert.equal(canonicalMapped.expected, aiFinding.expected);
assert.equal(canonicalMapped.actual, aiFinding.actual);
assert.equal(canonicalMapped.check, aiFinding.check);
assert.equal(canonicalMapped.tableOrSection, aiFinding.table_or_section);
assert.equal(canonicalMapped.amountsProvenance, "model_claimed_not_verified");
assert.deepEqual(canonicalMapped.calculationBasis, aiFinding.calculation_basis);

console.log("canonical AI explanation mapping passed");
