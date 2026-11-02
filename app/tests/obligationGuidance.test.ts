import assert from "node:assert/strict";

import { obligationGuidance } from "../app/components/review-workbench/obligationGuidance";
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
assert.match(structuredGuidance.blocker ?? "", /缺少：FIN_03_expenditure/);
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
