import assert from "node:assert/strict";

import {
  displayRecommendedAction,
  displayReviewReasonTitle,
  displayTableName,
  extractReviewDiagnostics,
  getDisplayIssueTotal,
  toUiProblems,
} from "../lib/uiAdapters";
import { evidenceExcerptLabel } from "../lib/evidenceIdentity";

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
  mergeConflict[0].evidenceRefs?.map((ref) => ref.page),
  [2, 3],
  "merge conflict should preserve both source page references for review",
);
assert.deepEqual(
  mergeConflict[0].evidenceRefs?.map((ref) => ref.bbox),
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
  "科目名称与主体口径",
  "BUD-109 should share its business category with AI name-alignment findings",
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
assert.equal(structuredReviewProblems.length, 1, "structured review items are diagnostics, not business problems");
assert.equal(structuredReviewProblems[0].id, "rule-1");
assert.ok(!structuredReviewProblems.some((problem) => problem.id.startsWith("review-")),
  "technical structured identifiers must not appear in the business problem list");
assert.equal(
  getDisplayIssueTotal({ merged_issue_total: 20, issue_total: 20, review_item_count: 4 }),
  20,
  "task totals must not add technical structured review items",
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

for (const [evidenceStatus, sourceComplete, expected] of [
  ["complete", true, true],
  ["complete", false, false],
  ["incomplete_rule_warning", true, false],
  [undefined, false, false],
] as const) {
  const [problem] = toUiProblems({
    job_id: "job-source-identity",
    result: { rule_findings: [{
      id: "source-identity", source: "rule", rule_id: "SOURCE-IDENTITY", severity: "warn",
      title: "Amount comparison", message: "Amount comparison", location: { page: 1 },
      evidence: [{ page: 1, text: "narrative=10, table=20" }],
      evidence_status: evidenceStatus,
      evidence_verification: { status: "verified", source_identity_complete: sourceComplete },
    }] },
  } as any);
  assert.equal(problem.sourceIdentityVerified, expected);
  assert.equal(evidenceExcerptLabel(problem.sourceIdentityVerified === true),
    expected ? "原文证据" : "候选证据/计算说明（原文绑定未完成）");
  assert.equal(problem.snippet, "narrative=10, table=20", "retain unverified observations for review without promoting their source status");
}

// 旧版 AI 结果标题落成英文机器代码时，展示检查项名称（标题、摘要、说明一致）。
{
  const [problem] = toUiProblems({
    job_id: "job-generic-title",
    result: { ai_findings: [{
      id: "ai-generic", source: "ai", rule_id: "AI-SEM-002", severity: "manual_review",
      title: "待人工复核：generic", check: "机构数量与列举项一致性",
      message: "第7页“机构设置”发现待人工复核：generic。命中文本：设 22 个内设机构",
      display: { summary: "待人工复核：generic" },
      evidence: [{ page: 7, text: "设 22 个内设机构" }], location: { page: 7 },
    }] },
  } as any);
  assert.equal(problem.title, "待人工复核：机构数量与列举项一致性");
  assert.equal(problem.description, "第7页“机构设置”发现待人工复核：机构数量与列举项一致性。命中文本：设 22 个内设机构");
  // 没有检查项名称时不猜，保持原样。
  const [untouched] = toUiProblems({
    job_id: "job-generic-title-2",
    result: { ai_findings: [{ id: "ai-generic-2", source: "ai", rule_id: "AI-SEM-002", severity: "manual_review",
      title: "待人工复核：generic", message: "m", evidence: [{ page: 1, text: "x" }], location: { page: 1 } }] },
  } as any);
  assert.equal(untouched.title, "待人工复核：generic");
}

// 2026-11-18 系统缺口修复：英文诊断代码、建议动作与未识别表名必须中文化，且可定位到页码。
{
  assert.equal(displayReviewReasonTitle("table_reading_incomplete"), "部分表格读取未完成");
  assert.equal(displayReviewReasonTitle("rules_not_executed"), "部分规则未执行");
  assert.ok(displayRecommendedAction("inspect_table_column_mapping").includes("列头"));
  assert.equal(displayTableName("UNKNOWN_P7_T0"), "第 7 页未识别表格");

  const diagnostics = extractReviewDiagnostics({
    job_id: "job-diag-cn",
    review_report: {
      schema_version: 3,
      diagnostics: [
        {
          id: "reading:mapping:bc4af0d603379fb7",
          record_type: "structured_review",
          table_code: "FIN_02_income",
          page_numbers: [6, 7],
          message: "第 7 页《收入决算表》映射不完整",
          recommended_action: "inspect_table_column_mapping",
        },
        {
          record_type: "unfinished_check",
          obligation_id: "OBL-TXT-FINAL-DETAIL",
          title: "支出决算具体情况逐项细化到项、分项加总、不漏列",
          detail: "另有1处金额缺少可归一单位或格式（定位：第21页「支出决算65.96 为万元」）",
        },
        {
          record_type: "processing_notice",
          code: "rules_not_executed",
          title: "rules_not_executed",
          message: "适用规则中仅 82/90 条得出结论",
          pages: [7],
        },
      ],
    },
  });
  assert.equal(diagnostics.length, 3);
  assert.equal(diagnostics[0].title, "表格识别待复核：收入决算表");
  assert.ok(diagnostics[0].suggestion?.includes("列头"));
  assert.equal(diagnostics[0].page, 6);
  assert.equal(diagnostics[1].page, 21);
  assert.equal(diagnostics[2].title, "部分规则未执行");
  assert.equal(diagnostics[2].page, 7);

  // 旧版 v2 review_report 兼容投影：中文标题、中文建议与单元格定位坐标。
  const legacyNotices = toUiProblems({
    job_id: "job-v2-compat",
    review_report: {
      schema_version: 2,
      issues: [],
      tips: [],
      manual_review: [
        {
          record_type: "review_reason",
          code: "table_reading_incomplete",
          title: "table_reading_incomplete",
          message: "还有 0 处表格读取缺口",
          pages: [7],
        },
        {
          record_type: "structured_review",
          id: "reading:mapping:x",
          table_code: "UNKNOWN_P7_T0",
          title: "结构化识别待复核：UNKNOWN_P7_T0",
          message: "第 7 页映射不完整",
          recommended_action: "inspect_table_column_mapping",
          cells: [{ page: 7, bbox: [10, 20, 30, 40], raw_text: "55.31" }],
        },
      ],
    },
  } as any);
  assert.equal(legacyNotices.length, 2);
  assert.equal(legacyNotices[0].title, "部分表格读取未完成");
  assert.equal(legacyNotices[0].page, 7);
  assert.equal(legacyNotices[1].title, "表格识别待复核：第 7 页未识别表格");
  assert.ok(legacyNotices[1].suggestion.includes("列头"));
  assert.equal(legacyNotices[1].page, 7);
  assert.deepEqual(legacyNotices[1].evidenceRefs?.[0]?.bbox, [10, 20, 30, 40]);
}

  // 漏单位已由同科目原表核对的，原表侧凭证要成为可点击证据（跳第16页）。
  const verifiedBasis = toUiProblems({
    job_id: "job-basis",
    review_report: {
      schema_version: 3,
      issues: [{
        id: "CMM-011-1",
        rule_id: "CMM-011",
        severity: "warn",
        title: "金额缺少单位“万元”",
        message: "金额缺少单位“万元”：“支出决算为 5,465.41”。；已按第16页核对",
        evidence: [{ page: 26, bbox: [1, 2, 3, 4], verification_status: "verified", location_level: "exact_span", quote: "支出决算为 5,465.41" }],
        location: {
          page: 26,
          verified_by: {
            table_page: 16,
            table_bbox: [278, 615, 353, 630],
            table_value: "5,465.41",
            table_quote: "5,465.41",
            subject_code: "2160250",
            subject_name: "事业运行",
            sub_check_id: "V33-251:final:item_sum",
          },
        },
      }],
      tips: [],
      manual_review: [],
    },
  } as any);
  const basisRef = verifiedBasis[0]?.evidenceRefs?.find((ref) => ref.role === "原表科目决算依据");
  assert.ok(basisRef);
  assert.equal(basisRef?.page, 16);
  assert.deepEqual(basisRef?.bbox, [278, 615, 353, 630]);
  assert.ok(basisRef?.locationText.includes("2160250"));


console.log("uiAdapters dual-mode tests passed");
// ---------------------------------------------------------------------------
// P24（2026-11-21 第三轮）：同页两科目"去处理"逐笔对应——科目绑定
// ---------------------------------------------------------------------------
import { isProblemSubjectBound } from "../lib/uiAdapters";

const cultureGroup = {
  subject: "文化产业发展专项支出",
  relatedIssueIds: [] as string[],
};
const retireGroup = {
  subject: "归口管理的行政单位离退休",
  relatedIssueIds: ["issue-2080501"],
};
// 208-05-01 离退休正式问题（与文化产业卡同页同规则同检查项）
const retireIssue = {
  id: "issue-2080501",
  actualName: "归口管理的行政单位离退休",
  title: "说明5项级科目名称与T5不一致",
  snippet: "表格“行政单位离退休”，说明“归口管理的行政单位离退休”",
};
// 207 类名称差异问题（实际名称是类名，不提项名）
const classIssue = {
  id: "issue-207",
  actualName: "文化体育与传媒支出",
  title: "说明5类级科目名称与T5不一致",
  snippet: "表格“文化旅游体育与传媒支出”，说明“文化体育与传媒支出”",
};

assert.equal(
  isProblemSubjectBound(cultureGroup, retireIssue), false,
  "同页同规则的另一科目问题不得成为文化产业卡的\"去处理\"目标",
);
assert.equal(
  isProblemSubjectBound(cultureGroup, classIssue), false,
  "类名问题不含组科目时也不绑定（保留定位与状态，取消错误入口）",
);
assert.equal(
  isProblemSubjectBound(retireGroup, retireIssue), true,
  "related_issue_ids 命中的问题直接采纳",
);
assert.equal(
  isProblemSubjectBound(retireGroup, classIssue), false,
  "同页其他科目问题不得成为离退休卡的目标",
);
// 历史快照没有 related_issue_ids：退回科目名比对
assert.equal(
  isProblemSubjectBound(
    { subject: "归口管理的行政单位离退休", relatedIssueIds: undefined }, retireIssue,
  ), true, "无 related_issue_ids 时按科目名比对命中",
);
// 组没有科目锚点（整节加总）：不受科目约束
assert.equal(
  isProblemSubjectBound({ subject: "", relatedIssueIds: [] }, classIssue), true,
  "空科目组不受科目约束",
);
// 聚合问题经 memberIssueIds 命中后端关联
assert.equal(
  isProblemSubjectBound(retireGroup, { id: "group-x", memberIssueIds: ["issue-2080501"] }),
  true, "聚合成员命中 related_issue_ids 时采纳",
);
// 第三轮自检加固：related_issue_ids 是完整约束——列表外的问题即使摘句里
// 含本科目名（同页长句同时含两科目）也不得绑定，否则互跳会重新打开。
const compositeSentenceIssue = {
  id: "issue-composite",
  actualName: "文化体育与传媒支出",
  title: "同页两科目金额不一致",
  snippet:
    "文化体育与传媒支出（类）……文化产业发展专项支出（项）……归口管理的行政单位离退休（项）……",
};
assert.equal(
  isProblemSubjectBound(retireGroup, compositeSentenceIssue), false,
  "related_issue_ids 在册时，摘句含本科目名的列表外问题仍不得绑定",
);
assert.equal(
  isProblemSubjectBound(cultureGroup, compositeSentenceIssue), false,
  "related_issue_ids 为空（未核证到唯一对应）时同页复合句问题仍不绑定",
);
console.log("P24 subject-binding tests passed");
