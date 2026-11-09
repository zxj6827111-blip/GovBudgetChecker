import assert from "node:assert/strict";

import { toUiProblems } from "../lib/uiAdapters";
import {
  buildAmountComparison,
  buildCalculationGroups,
  detailSummary,
} from "../app/components/review-workbench/reviewReadingAdapters";
import {
  composeDecisionNote,
  isIgnoreReasonComplete,
  parseDecisionNote,
  stripIgnoreReason,
} from "../app/components/review-workbench/reviewDecisionNotes";

// 与真实 V33-244 落库结果同形：合计与三个分项都带 current_value 和精确坐标。
function cell(role: string, operand: number, field: string, value: string) {
  return {
    role,
    relation_id: "three_public:决算:total_actual:sum",
    operand_id: `three_public:决算:total_actual:sum:${operand}`,
    current_value: value,
    table: "FIN_07_three_public",
    row: "合计",
    field,
    subject_code: "total",
    subject_name: "合计",
    physical_page: 15,
    page: 15,
    bbox: [129.6, 218.7, 187.7, 247.1],
    location_level: "exact_cell",
    verification_status: "verified",
  };
}

const sumIssue = {
  id: "issue-v33-244",
  rule_id: "V33-244",
  source: "rule",
  severity: "info",
  title: "三公经费决算total_actual分项合计",
  message: "三公经费决算total_actual分项合计：表内合计41.24万元，分项之和41.23万元，差额0.01万元。可能为取整误差。",
  location: {
    page: 15,
    table_refs: [
      cell("parent", 0, "total_actual", "41.24"),
      cell("child", 1, "overseas", "29.31"),
      cell("child", 2, "vehicle_purchase_operation", "9.12"),
      cell("child", 3, "reception", "2.80"),
    ],
  },
  evidence_verification: { status: "verified", source_identity_complete: true },
};

function mapOne(issue: Record<string, unknown>) {
  const detail: any = {
    job_id: "job-reading",
    result: {
      merged: { merge_policy_version: "fact-check-relation-v1", merged_ids: [issue.id] },
      rule_findings: [issue],
      ai_findings: [],
    },
    structured_ingest: {},
    ignored_issue_ids: [],
  };
  const [problem] = toUiProblems(detail);
  assert.ok(problem);
  return problem;
}

const sumProblem = mapOne(sumIssue);
const groups = buildCalculationGroups(sumProblem);
assert.equal(groups.length, 1);
const [group] = groups;
assert.equal(group.total.valueText, "41.24");
assert.deepEqual(group.items.map((item) => item.valueText), ["29.31", "9.12", "2.80"]);
assert.equal(group.itemsSum, "41.23");
assert.equal(group.difference, "0.01");
// 各项标签只保留区分它们的部分，不重复表名和“合计”行名。
assert.equal(new Set(group.items.map((item) => item.label)).size, 3);
for (const row of [group.total, ...group.items]) {
  assert.ok(!row.label.includes("合计 · "), row.label);
  assert.equal(row.page, 15);
  assert.equal(row.located, true);
}
// 每一行都能回到原文：key 必须是问题证据引用里的 key。
const refKeys = new Set(sumProblem.evidenceRefs?.map((ref) => ref.key));
for (const row of [group.total, ...group.items]) assert.ok(refKeys.has(row.key));

assert.deepEqual(buildAmountComparison(sumProblem, groups), {
  labels: ["表内合计", "明细合计", "差额"],
  values: ["41.24", "41.23", "0.01"],
});

// 认证空白是可定位的计算0；不同规则引用同一组原格时只展一组。
const blankRefs = sumIssue.location.table_refs.map((ref, index) => ({
  ...ref, source_cell_id: `native:r1c${index}`, current_value: index === 3 ? "0.00" : ref.current_value,
  blank_as_zero: index === 3,
}));
const duplicateRefs = blankRefs.map((ref, index) => ({ ...ref,
  relation_id: "other-rule:same-formula", operand_id: `other:${index}`,
}));
const [blankGroup] = buildCalculationGroups(mapOne({ ...sumIssue,
  location: { table_refs: [...blankRefs, ...duplicateRefs] },
}));
assert.equal(buildCalculationGroups(mapOne({ ...sumIssue,
  location: { table_refs: [...blankRefs, ...duplicateRefs] },
})).length, 1);
assert.equal(blankGroup.items[2].blankAsZero, true);
assert.equal(blankGroup.items[2].located, true);
assert.equal(blankGroup.itemsSum, "38.43");
const otherColumn = duplicateRefs.map((ref) => ({ ...ref, field: `${ref.field}_different`, source_cell_id: `${ref.source_cell_id}-different` }));
assert.equal(buildCalculationGroups(mapOne({ ...sumIssue,
  location: { table_refs: [...blankRefs, ...otherColumn] },
})).length, 2);

// 同一原行的三列并排，201的基本空白0和项目3不能互相替代。
const columnCell = (code: string, indicator: string, value: string, blank = false) => ({
  key: `expenditure:T3:${code}:${indicator}`, indicator, value, page: 12,
  bbox: [10, 20, 30, 40], blank_as_zero: blank,
});
const threeColumnContext = {
  verified: true, checked_indicator: "basic_actual",
  rows: [
    { code: "total", name: "合计", row_status: "mismatch", row_difference: "-1", cells: [
      columnCell("total", "total_actual", "10"), columnCell("total", "basic_actual", "8"), columnCell("total", "project_actual", "3")] },
    { code: "201", name: "一般公共服务支出", row_status: "ok", row_difference: "0", cells: [
      columnCell("201", "total_actual", "3"), columnCell("201", "basic_actual", "0", true), columnCell("201", "project_actual", "3")] },
    { code: "208", name: "社会保障和就业支出", row_status: "ok", row_difference: "0", cells: [
      columnCell("208", "total_actual", "7"), columnCell("208", "basic_actual", "7"), columnCell("208", "project_actual", "0", true)] },
  ],
  columns: [
    { indicator: "total_actual", actual: "10", computed: "10", difference: "0", status: "ok" },
    { indicator: "basic_actual", actual: "8", computed: "7", difference: "1", status: "mismatch" },
    { indicator: "project_actual", actual: "3", computed: "3", difference: "0", status: "ok" },
  ],
};
const columnsProblem = mapOne({ ...sumIssue, location: { ...sumIssue.location, expenditure_context: threeColumnContext } });
assert.equal(columnsProblem.expenditureContext?.rows.length, 3);
assert.equal(columnsProblem.expenditureContext?.rows[1].cells[1].blankAsZero, true);
assert.equal(columnsProblem.expenditureContext?.rows[1].cells[2].value, "3");
for (const row of columnsProblem.expenditureContext!.rows) for (const cell of row.cells)
  assert.ok(columnsProblem.evidenceRefs?.some((ref) => ref.key === cell.key && ref.page === cell.page && ref.bbox));
assert.equal(mapOne({ ...sumIssue, location: { expenditure_context: { ...threeColumnContext, verified: false } } }).expenditureContext, undefined);

// 任一金额无法解析时不给出半截加总。
const brokenProblem = mapOne({
  ...sumIssue,
  id: "issue-broken",
  location: { page: 15, table_refs: [cell("parent", 0, "total_actual", "41.24"), cell("child", 1, "overseas", "—")] },
});
const [brokenGroup] = buildCalculationGroups(brokenProblem);
assert.equal(brokenGroup.itemsSum, null);
assert.equal(brokenGroup.difference, null);
assert.equal(buildAmountComparison(brokenProblem, [brokenGroup]), null);

// 只有总额定位、没有逐项操作数的规则（如 V33-251）不生成计算明细。
const textOnly = mapOne({
  id: "issue-v33-251",
  rule_id: "V33-251",
  source: "rule",
  severity: "medium",
  title: "支出决算具体情况逐项列示的年初预算合计31743.94万元，与该部分所列年初预算总额32043.94万元不一致，相差300.00万元",
  message: "支出决算具体情况逐项列示的年初预算合计31743.94万元，与该部分所列年初预算总额32043.94万元不一致，相差300.00万元（可能漏列科目）。",
  location: { page: 19, table_refs: [{ role: "原文位置1", page: 19, span: "年初预算为 32043.94 万元，" }] },
});
assert.deepEqual(buildCalculationGroups(textOnly), []);
assert.equal(buildAmountComparison(textOnly, []), null);
assert.equal(detailSummary(textOnly.title, textOnly.description), "可能漏列科目");

// 问题自带当前值/应为/差额时优先使用。
assert.deepEqual(buildAmountComparison({ ...textOnly, actual: "141.12", expected: "165.80", difference: "-24.68" }, []), {
  labels: ["当前值", "应为", "差额"],
  values: ["141.12", "165.80", "-24.68"],
});

assert.equal(detailSummary("重复词", "重复词"), "");
assert.equal(detailSummary("重复词", "重复词。"), "");
assert.equal(detailSummary("标题", "完全不同的说明。"), "完全不同的说明。");

// 忽略理由写在 note 开头，补充意见原样保留。
const note = composeDecisionNote({ label: "口径不同", detail: "第 19 页按\n全口径列示" }, "已与单位电话核实");
assert.equal(note, "忽略理由：口径不同\n说明：第 19 页按 全口径列示\n\n已与单位电话核实");
assert.deepEqual(parseDecisionNote(note), {
  ignoreReason: { label: "口径不同", detail: "第 19 页按 全口径列示" },
  userNote: "已与单位电话核实",
});
assert.equal(stripIgnoreReason(note), "已与单位电话核实");
assert.equal(composeDecisionNote({ label: "取整误差", detail: "" }, ""), "忽略理由：取整误差");
assert.deepEqual(parseDecisionNote("忽略理由：取整误差"), { ignoreReason: { label: "取整误差", detail: "" }, userNote: "" });
assert.deepEqual(parseDecisionNote("普通意见\n第二行"), { ignoreReason: null, userNote: "普通意见\n第二行" });
assert.deepEqual(parseDecisionNote(null), { ignoreReason: null, userNote: "" });

assert.equal(isIgnoreReasonComplete("rounding", ""), true);
assert.equal(isIgnoreReasonComplete("other", "  "), false);
assert.equal(isIgnoreReasonComplete("other", "已在附表说明"), true);
assert.equal(isIgnoreReasonComplete("unknown", "x"), false);

console.log("reviewReading tests passed");
