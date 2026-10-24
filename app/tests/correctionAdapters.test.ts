import assert from "node:assert/strict";

import {
  cellLocation,
  countCorrectionTargets,
  describeReadingReason,
  describeSubmitError,
  toCorrectionChanges,
  upsertPending,
  validateCellValue,
  type CorrectionTable,
  type CorrectionTargets,
  type PendingCorrection,
} from "../app/components/review-workbench/correctionAdapters";

// 「读取校正」页签纯逻辑：证据位置的取用顺序、提交载荷、金额校验与失败原因。
const table: CorrectionTable = {
  fragment_uid: "t1", instance_uid: "t1", table_code: "FIN_02_income", title: "收入决算表", page: 8,
  bbox: [10, 10, 500, 700], header_row_count: 1, extraction_method: "ocr", scope: "core",
  mapping_status: "mapping_incomplete", column_mappings: [{ source_col_idx: 2, canonical_measure: "total_actual" }],
  rows: [["编码", "名称", "合计"], ["206", "科学技术支出", "6,194,400"], ["221", "", ""]],
  row_boxes: [null, null, { page: 9, bbox: [10, 40, 500, 60] }],
  unresolved_money_cells: [],
  unqualified_ocr_cells: [{ row: 1, column: 2, raw_text: "6,194,400", page: 8, bbox: [300, 20, 400, 30] }],
  arithmetic_conflicts: [], measure_options: [{ measure: "total_actual", label: "合计" }],
};

// 位置：格自身坐标 → 所在行的原页区域（续页行落在第 9 页）→ 整张表。
assert.deepEqual(cellLocation(table, 1, 2), { physical_page: 8, bbox: [300, 20, 400, 30] });
assert.deepEqual(cellLocation(table, 2, 1), { physical_page: 9, bbox: [10, 40, 500, 60] });
assert.deepEqual(cellLocation(table, 0, 0), { physical_page: 8, bbox: [10, 10, 500, 700] });
assert.equal(cellLocation({ ...table, bbox: null }, 0, 0), null);

const cell: PendingCorrection = {
  kind: "cell", tableUid: "t1", tableTitle: "收入决算表", row: 2, column: 1, previous: "", value: " 住房保障支出 ",
  location: { physical_page: 9, bbox: [10, 40, 500, 60] },
};
// 同一格只保留最后一次修改。
const pending = upsertPending(upsertPending([], { ...cell, value: "错" }), cell);
assert.equal(pending.length, 1);
assert.deepEqual(toCorrectionChanges(pending), [{
  target_type: "table_instance", target_id: "t1", field: "cell_value",
  value: { row_idx: 2, col_idx: 1, raw_value: "住房保障支出" }, location: { physical_page: 9, bbox: [10, 40, 500, 60] },
}]);

assert.equal(validateCellValue("1,234.56", true), null);
assert.equal(validateCellValue("-12", true), null);
assert.match(String(validateCellValue("一千", true)), /只能填写数字/);
assert.equal(validateCellValue("住房保障支出", false), null);
assert.match(String(validateCellValue("  ", false)), /请填写/);

assert.match(describeReadingReason("rule_admission_blocked", "候选事实的完整业务身份未确认"), /补录数字无法解决/);
assert.equal(describeReadingReason("source_label_missing"), "这一行缺少科目名称");
assert.equal(describeSubmitError(409, { detail: { message: "提交基于旧分析版本，请刷新后重新校正" } }),
  "提交基于旧分析版本，请刷新后重新校正");
assert.equal(describeSubmitError(422, { detail: "manual_location bbox exceeds the physical page" }),
  "manual_location bbox exceeds the physical page");

const targets: CorrectionTargets = {
  job_id: "j", analysis_basis_token: "j:r", tables: [table], orphaned_amounts: [], history: [],
  gaps: [{ id: "a", page: 8, reason: "native_amount_not_extracted", bbox: null, affected_fragment_uids: ["t1"] },
         { id: "b", page: 3, reason: "image_region_not_structured", bbox: null, affected_fragment_uids: [] }],
};
assert.equal(countCorrectionTargets(targets), 2);

console.log("correctionAdapters.test.ts passed");
