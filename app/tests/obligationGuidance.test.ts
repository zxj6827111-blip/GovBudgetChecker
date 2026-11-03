import assert from "node:assert/strict";

import { describeObligationDiagnosis, obligationGuidance } from "../app/components/review-workbench/obligationGuidance";
import type { ObligationReviewItemRecord } from "../app/components/review-workbench/reviewLifecycleAdapters";

/**
 * 补核提示的纯逻辑契约。
 *
 * 三条红线：
 * - 分类只是提示，不替用户下结论；不确定时必须是「需人工判断」；
 * - 同源只提示，不自动合并表态（revision 仍是每条独立的）；
 * - 已知义务没登记、旧后端没有 basis/checkers 时仍要能渲染。
 */

function item(overrides: Partial<ObligationReviewItemRecord> = {}): ObligationReviewItemRecord {
  return {
    obligation_id: "OBL-A",
    group_id: "TABLE_CROSS",
    group_title: "表间关系",
    title: "义务 A",
    status: "insufficient_data",
    reason: "insufficient_data",
    reason_label: "取数不足",
    detail: "",
    decision: null,
    decision_revision: null,
    decided_by: null,
    decided_at: null,
    note: null,
    evidence_reference: null,
    ...overrides,
  };
}

// ---- 已知义务：封面年度是规则阈值，不是材料错误 --------------------------------

const cover = item({
  obligation_id: "OBL-STRUCT-COVER",
  title: "封面要素：报告年度、单位全称、文种",
  detail: "封面年度缺少正文独立依据",
  basis: "封面须含单位全称、报告年度与部门/单位文种标识（清单 A-02）",
});
const coverGuidance = obligationGuidance(cover, [cover]);
assert.equal(coverGuidance.category, "limitation");
assert.equal(coverGuidance.categoryLabel, "疑似取数限制");
assert.match(coverGuidance.whatToConfirm, /年度/);
assert.match(coverGuidance.howToDecide, /核对无误/);
assert.equal(coverGuidance.requirement, "封面须含单位全称、报告年度与部门/单位文种标识（清单 A-02）");
assert.equal(coverGuidance.sameSource, null);

// ---- 同源：空表说明 + 财政拨款总表合计 ---------------------------------------

const emptyStatement = item({
  obligation_id: "OBL-STRUCT-EMPTY-STATEMENT",
  title: "空表/无此项说明与总表零值确认",
  detail: "财政拨款收入支出决算总表 第21行第6列为空白，不能按零计算",
});
const fiscalTotal = item({
  obligation_id: "OBL-CROSS-FISCAL-TOTAL",
  title: "财政拨款收支总表合计一致性",
  detail: "十九、住房保障支出的government_fund_budget/决算操作数缺失或不唯一",
});
const emptyGuidance = obligationGuidance(emptyStatement, [emptyStatement, fiscalTotal]);
assert.deepEqual(emptyGuidance.sameSource?.obligationIds, ["OBL-CROSS-FISCAL-TOTAL"]);
assert.match(emptyGuidance.sameSource?.note ?? "", /同一处空白金额格/);
const fiscalGuidance = obligationGuidance(fiscalTotal, [emptyStatement, fiscalTotal]);
assert.deepEqual(fiscalGuidance.sameSource?.obligationIds, ["OBL-STRUCT-EMPTY-STATEMENT"]);

// 只有其中一条时不得凭空造出同源
assert.equal(obligationGuidance(emptyStatement, [emptyStatement]).sameSource, null);

// ---- 材料信号：T5 旧科目名，并关联已存在的问题 -------------------------------

const t5 = item({
  obligation_id: "OBL-TXT-T5",
  title: "一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
  detail: "说明中的款“行政事业单位离退休”在表内找不到同名科目（可能使用了旧科目名称或写错），无法与表格金额对应",
  checkers: ["V33-106", "V33-222", "V33-227"],
});
const t5Guidance = obligationGuidance(t5, [t5]);
assert.equal(t5Guidance.category, "material");
assert.equal(t5Guidance.categoryLabel, "疑似材料问题");
assert.match(t5Guidance.whatToConfirm, /科目名称/);
assert.match(t5Guidance.howToDecide, /旧科目名/);
assert.deepEqual(t5Guidance.sameSource?.findingIds, ["V33-227"]);
assert.match(t5Guidance.sameSource?.note ?? "", /V33-227/);

// T5 + 支出决算具体情况同时待补核 ⇒ 追加同源义务
const finalDetail = item({
  obligation_id: "OBL-TXT-FINAL-DETAIL",
  title: "支出决算具体情况逐项细化到项、分项加总、不漏列",
  detail: "支出决算具体情况漏列科目",
});
const t5WithDetail = obligationGuidance(t5, [t5, finalDetail]);
assert.ok(t5WithDetail.sameSource?.obligationIds.includes("OBL-TXT-FINAL-DETAIL"));

// ---- 未知义务：回落到通用提示，不擅自归类 -----------------------------------

const unknown = item({
  obligation_id: "OBL-UNKNOWN",
  title: "某条新义务",
  detail: "引擎没有给出可识别的具体原因",
  basis: "某条检查依据",
});
const unknownGuidance = obligationGuidance(unknown, [unknown]);
assert.equal(unknownGuidance.category, "review");
assert.equal(unknownGuidance.categoryLabel, "需人工判断");
assert.equal(unknownGuidance.requirement, "某条检查依据");
assert.match(unknownGuidance.whatToConfirm, /核对原文/);
assert.equal(unknownGuidance.sameSource, null);

// ---- 旧后端：没有 basis / checkers 也能渲染 ---------------------------------

const legacy = item({ obligation_id: "OBL-STRUCT-COVER", detail: "封面年度缺少正文独立依据" });
const legacyGuidance = obligationGuidance(legacy);
assert.equal(legacyGuidance.requirement, null);
assert.ok(legacyGuidance.whatToConfirm.length > 0);
assert.ok(legacyGuidance.evidenceExample.length > 0);

// ---- DH-03a：结构化字段优先于旧 detail 正则 -------------------------------

const structured = item({
  obligation_id: "OBL-INTERNAL-T3-TOTAL",
  detail: "",
  reason_codes: ["operand_missing", "cell_unbound"],
  missing_operands: [{
    source: "table",
    role: "project_actual",
    table_code: "FIN_03_expenditure",
    page: 7,
    row_key: "208",
    column: "project_actual",
    stage: "决算",
    year: 2025,
  }],
  blocked_by: [],
  candidates: [],
  next_action: "system_recheck",
});
const structuredGuidance = obligationGuidance(structured);
assert.equal(structuredGuidance.category, "limitation");
assert.match(structuredGuidance.blocker ?? "", /缺少：表FIN_03_expenditure/);
assert.match(structuredGuidance.blocker ?? "", /208/);
assert.match(structuredGuidance.blocker ?? "", /下一步：系统定向补取/);

const materialByName = item({
  obligation_id: "OBL-TXT-T5",
  detail: "",
  reason_codes: ["subject_name_unlocated"],
  missing_operands: [{
    source: "text",
    role: "行政事业单位离退休",
    section: "一般公共预算财政拨款支出决算情况说明",
    page: 12,
  }],
  next_action: "human_confirm",
});
assert.equal(obligationGuidance(materialByName).category, "material");
assert.match(obligationGuidance(materialByName).blocker ?? "", /说明章节|一般公共预算/);


// ---- F5：文字操作数的角色/页码、候选内容与保留的业务说明 -------------------

const textOperand = item({
  obligation_id: "OBL-NARRATIVE-AMOUNT",
  detail: "",
  reason_codes: ["subject_name_unlocated"],
  missing_operands: [{
    source: "text",
    role: "住房公积金决算金额",
    section: "支出决算具体情况",
    page: 12,
  }],
  candidates: [
    { source: "text", section: "支出决算具体情况", page: 12, span: [10, 20], value: "10.00" },
    { source: "text", section: "支出决算具体情况", page: 13, span: [30, 40], value: "20.00" },
  ],
  next_action: "human_confirm",
});
const textBlock = describeObligationDiagnosis(textOperand) ?? "";
assert.match(textBlock, /说明「支出决算具体情况」第12页/);
assert.match(textBlock, /角色：住房公积金决算金额/);
assert.match(textBlock, /说明「支出决算具体情况」第12页「10\.00」/);
assert.match(textBlock, /第13页「20\.00」/);
assert.doesNotMatch(textBlock, /候选：2 处/);

const noReason = item({
  obligation_id: "OBL-TXT-T5",
  detail: "原因码：规则未给出具体原因；规则说明：科目“住房公积金”决算金额未唯一取得；下一步：技术修复解析/规则",
  reason_codes: ["rule_no_reason"],
  missing_operands: [],
  candidates: [],
  next_action: "tech_fix",
});
assert.match(describeObligationDiagnosis(noReason) ?? "", /规则说明：科目“住房公积金”决算金额未唯一取得/);

// ---- F4：同源提示改用冻结原因码；旧结果缺字段才回退 detail -------------------

const t5Structured = item({
  obligation_id: "OBL-TXT-T5",
  detail: "判断不了的原因：疑似旧科目名无法在文内定位。",
  reason_codes: ["subject_name_unlocated"],
  legacy_coverage_terms: [],
  checkers: ["V33-106", "V33-222", "V33-227"],
});
const t5StructuredGuidance = obligationGuidance(t5Structured, [t5Structured, finalDetail]);
assert.deepEqual(t5StructuredGuidance.sameSource?.findingIds, ["V33-227"]);
assert.ok(t5StructuredGuidance.sameSource?.obligationIds.includes("OBL-TXT-FINAL-DETAIL"));

// [] 是合法的新结果：不得因为 detail 里还有旧句式就重新触发。
const emptyCodes = item({
  obligation_id: "OBL-TXT-T5",
  detail: "说明中的款“行政事业单位离退休”在表内找不到同名科目",
  reason_codes: [],
  legacy_coverage_terms: [],
  checkers: ["V33-227"],
});
assert.equal(obligationGuidance(emptyCodes, [emptyCodes]).sameSource, null);

// 旧后端缺 reason_codes 字段时才回退旧 detail 正则。
const missingCodes = item({
  obligation_id: "OBL-TXT-T5",
  detail: "说明中的款“行政事业单位离退休”在表内找不到同名科目",
  checkers: ["V33-227"],
});
assert.deepEqual(obligationGuidance(missingCodes, [missingCodes]).sameSource?.findingIds, ["V33-227"]);

// text_binding_failed 只有在同时保留了冻结覆盖词时才按同源处理。
const bindingWithTerms = item({
  obligation_id: "OBL-TXT-T5",
  detail: "判断不了的原因：说明条目绑不到表内科目。",
  reason_codes: ["text_binding_failed"],
  legacy_coverage_terms: ["住房公积金"],
  checkers: ["V33-227"],
});
assert.deepEqual(obligationGuidance(bindingWithTerms, [bindingWithTerms]).sameSource?.findingIds, ["V33-227"]);
const bindingWithoutTerms = item({
  obligation_id: "OBL-TXT-T5",
  detail: "判断不了的原因：说明条目绑不到表内科目。",
  reason_codes: ["text_binding_failed"],
  legacy_coverage_terms: [],
  checkers: ["V33-227"],
});
assert.equal(obligationGuidance(bindingWithoutTerms, [bindingWithoutTerms]).sameSource, null);

// ---- R1：真实 API 模型投影后的旧任务（reason_codes=[], legacy=null） ----------

const projectedOldTask = item({
  obligation_id: "OBL-TXT-T5",
  detail: "说明中的款“行政事业单位离退休”在表内找不到同名科目",
  reason_codes: [],
  legacy_coverage_terms: null,
  checkers: ["V33-106", "V33-222", "V33-227"],
});
assert.deepEqual(
  obligationGuidance(projectedOldTask, [projectedOldTask]).sameSource?.findingIds,
  ["V33-227"],
  "旧任务经当前 API 投影后 reason_codes 是 []，不能据此当成新结果",
);

// 新结果同样是 [] 原因码，但 legacy_coverage_terms 为列表 ⇒ 不读旧 detail。
const newTaskWithEmptyTerms = item({
  obligation_id: "OBL-TXT-T5",
  detail: "说明中的款“行政事业单位离退休”在表内找不到同名科目",
  reason_codes: [],
  legacy_coverage_terms: [],
  checkers: ["V33-227"],
});
assert.equal(obligationGuidance(newTaskWithEmptyTerms, [newTaskWithEmptyTerms]).sameSource, null);

// ---- R2：FIN_04 同源判断只认结构化/冻结依据 -------------------------------

const fin04Diagnostics = {
  reason_codes: ["cell_blank_semantics_unknown"],
  blocked_by: [{
    dimension: "cell",
    ref: "FIN04:row-total:col-expenditure",
    reason_code: "cell_blank_semantics_unknown",
  }],
  missing_operands: [{
    source: "table" as const,
    role: "expenditure_actual",
    table_code: "FIN_04_fiscal_grant_total",
    page: 7,
    row_key: "total",
    column: "expenditure_actual",
    fund_scope: "fiscal_appropriation",
    stage: "决算",
    year: 2025,
  }],
  candidates: [],
  next_action: "human_confirm",
  root_cause_key: "rc:identical-fin04-cell",
  legacy_coverage_terms: [],
};
const fin04Empty = item({
  ...fin04Diagnostics,
  obligation_id: "OBL-STRUCT-EMPTY-STATEMENT",
  title: "空表/无此项说明与总表零值确认",
  detail: "原文位置：FIN_04_fiscal_grant_total，第7页，合计行支出列。",
});
const fin04Total = item({
  ...fin04Diagnostics,
  obligation_id: "OBL-CROSS-FISCAL-TOTAL",
  title: "财政拨款收入支出决算总表合计一致性",
  detail: "对应原文位置见结构化操作数。",
});
const fin04Hint = (target: ObligationReviewItemRecord) =>
  obligationGuidance(target, [target, fin04Total]).sameSource;
assert.deepEqual(fin04Hint(fin04Empty)?.obligationIds, ["OBL-CROSS-FISCAL-TOTAL"]);

// 边界 1：仅改 detail 不得改变判断。
const fin04ProseOnly = item({
  ...fin04Empty,
  detail: "原文位置：第7页，合计行支出列；表身份见结构化操作数。",
});
const fin04NoTableName = item({
  ...fin04Empty,
  detail: "完全不同的结构化说明，不含表名或表代号。",
});
assert.deepEqual(fin04Hint(fin04ProseOnly), fin04Hint(fin04Empty));
assert.deepEqual(fin04Hint(fin04NoTableName), fin04Hint(fin04Empty));

// 边界 2：仅改“规则说明”不得改变判断；结构化依据相同，文案不同也同判。
const fin04RetainedFin04 = item({
  ...fin04Empty,
  detail: "规则说明：财政拨款收入支出决算总表金额未取得。下一步：人工核对。",
});
const fin04RetainedFund = item({
  ...fin04Empty,
  detail: "规则说明：政府性基金预算财政拨款收入支出决算表金额未取得。下一步：人工核对。",
});
assert.deepEqual(fin04Hint(fin04RetainedFin04), fin04Hint(fin04RetainedFund));
assert.deepEqual(fin04Hint(fin04RetainedFund), fin04Hint(fin04Empty));

// 边界 3：旧任务简称/表代号不得扩大第一阶段的空表匹配范围。
const fin04LegacyShort = item({
  ...fin04Empty,
  legacy_coverage_terms: null,
  reason_codes: [],
  blocked_by: [],
  missing_operands: [],
  candidates: [],
  detail: "财政拨款收支总表存在空白格。",
});
const fin04LegacyCode = item({
  ...fin04Empty,
  legacy_coverage_terms: null,
  reason_codes: [],
  blocked_by: [],
  missing_operands: [],
  candidates: [],
  detail: "FIN_04_fiscal_grant_total存在空白格。",
});
assert.equal(obligationGuidance(fin04LegacyShort, [fin04LegacyShort, fin04Total]).sameSource, null);
assert.equal(obligationGuidance(fin04LegacyCode, [fin04LegacyCode, fin04Total]).sameSource, null);

// 旧任务完整表名仍按原匹配器提示。
const fin04LegacyExact = item({
  ...fin04Empty,
  legacy_coverage_terms: null,
  reason_codes: [],
  blocked_by: [],
  missing_operands: [],
  candidates: [],
  detail: "财政拨款收入支出决算总表 没有可用金额事实",
});
assert.deepEqual(
  obligationGuidance(fin04LegacyExact, [fin04LegacyExact, fin04Total]).sameSource?.obligationIds,
  ["OBL-CROSS-FISCAL-TOTAL"],
);

// 新结果没有结构化 FIN_04 身份时，detail 里的全称也不能触发（不读 detail）。
const fin04NewWithoutStructured = item({
  ...fin04Empty,
  blocked_by: [],
  missing_operands: [],
  candidates: [],
  detail: "正在核对：财政拨款收入支出决算总表空白格。",
});
assert.equal(
  obligationGuidance(fin04NewWithoutStructured, [fin04NewWithoutStructured, fin04Total]).sameSource,
  null,
);

