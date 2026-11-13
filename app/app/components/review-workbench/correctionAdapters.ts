/**
 * correctionAdapters.ts：「读取校正」页签的纯逻辑层（无 JSX，供 jiti 单测直接 import）。
 *
 * 数据来自 `GET /api/jobs/{id}/correction-targets`：系统读不全的表（含文字矩阵）、
 * 原页上没读进表格的区域、以往的校正记录。审核员在这里做三件事：
 * - 补录/确认单元格（cell_value）：扫描件低分格、没读出来的格、缺科目名称的行；
 * - 指定某一列的含义（column_mapping）：金额没对上列；
 * - 每条校正都带原页位置（location），服务端据此生成人工证据，不需要先有系统证据。
 */

export interface CorrectionLocation {
  physical_page: number;
  bbox: number[];
}

export interface ReadingCell {
  source_cell_id?: string;
  raw_text: string;
  page: number;
  bbox: number[] | null;
  row: number;
  column: number;
  reason?: string | null;
  rule_rejection?: string | null;
  expected_indicator?: string | null;
  fact_uids?: string[];
  manual?: boolean;
  is_header?: boolean;
}

export interface CorrectionTable {
  fragment_uid: string;
  instance_uid: string | null;
  table_code: string;
  title: string;
  page: number;
  bbox: number[] | null;
  header_row_count: number;
  extraction_method: string;
  scope: string;
  mapping_status: string;
  column_mappings: { source_col_idx: number; canonical_measure: string; mapping_source?: string | null }[];
  rows: (string | null)[][];
  row_boxes?: ({ page: number; bbox: number[] } | null)[];
  unresolved_money_cells: ReadingCell[];
  unqualified_ocr_cells: ReadingCell[];
  arithmetic_conflicts: unknown[];
  measure_options: { measure: string; label: string }[];
}

export interface ReadingGap {
  id: string;
  page: number;
  reason: string;
  bbox: number[] | null;
  raw_text?: string;
  table_code?: string;
  affected_fragment_uids?: string[];
}

export interface CorrectionHistoryRecord {
  record_id: string;
  actor: string;
  reason: string;
  created_at: string;
  changes: { field?: string; target_id?: string; value?: unknown }[];
}

export interface CorrectionTargets {
  job_id: string;
  analysis_basis_token: string | null;
  reading_status?: string | null;
  tables: CorrectionTable[];
  table_code_options?: { code: string; label: string }[];
  gaps: ReadingGap[];
  orphaned_amounts: ReadingCell[];
  overlay_status?: { status?: string; unresolved?: { reason?: string }[] } | null;
  history: CorrectionHistoryRecord[];
}

const READING_REASON_LABELS: Record<string, string> = {
  amount_column_unmapped: "金额所在列没有对应到列含义",
  column_identity_unresolved: "列含义未确认",
  column_arithmetic_conflict: "合计与分项系统性对不上，列可能错位",
  fact_binding_conflict: "前后两次读取结果不一致",
  source_label_missing: "这一行缺少科目名称",
  fact_unbound: "金额没有生成数据",
  rule_admission_blocked: "数据未通过规则准入",
  physical_amount_missing_from_logical_view: "原表金额在合并后的表里丢失",
};

const GAP_REASON_LABELS: Record<string, string> = {
  native_amount_not_extracted: "原页有金额没有读进表格",
  table_title_without_usable_cells: "有表题，但表格内容没读出来",
  image_region_not_structured: "图片区域没有读成表格",
  native_decimal_tail_unresolved: "金额的小数位跨格，未能确认",
};

export function describeReadingReason(reason?: string | null, ruleRejection?: string | null): string {
  const label = READING_REASON_LABELS[String(reason ?? "")] ?? "需要人工核对";
  if (reason !== "rule_admission_blocked" || !ruleRejection) return label;
  // 身份未确认不是读数问题，补录数字无效，要先在元数据里确认主体、年度和口径。
  return ruleRejection.includes("身份")
    ? `${label}（${ruleRejection}；请先确认材料的主体、年度和口径，补录数字无法解决）`
    : `${label}（${ruleRejection}）`;
}

export function describeGap(gap: ReadingGap): string {
  const label = GAP_REASON_LABELS[gap.reason] ?? "表格区域读取不完整";
  return gap.raw_text ? `${label}：${gap.raw_text}` : label;
}

export function cellKey(row: number, column: number): string {
  return `${row}:${column}`;
}

export interface AttentionCell extends ReadingCell {
  kind: "amount" | "scan";
  label: string;
}

/** 表中需要人工处理的格：未落成数据的金额格 + 扫描识别未通过资格的非表头格。 */
export function attentionCells(table: CorrectionTable): Map<string, AttentionCell> {
  const result = new Map<string, AttentionCell>();
  for (const cell of table.unqualified_ocr_cells ?? []) {
    if (cell.is_header) continue;
    result.set(cellKey(cell.row, cell.column), { ...cell, kind: "scan", label: "扫描识别分数不足，需对照原页确认" });
  }
  for (const cell of table.unresolved_money_cells ?? []) {
    result.set(cellKey(cell.row, cell.column), {
      ...cell,
      kind: "amount",
      label: describeReadingReason(cell.reason, cell.rule_rejection),
    });
  }
  return result;
}

/** 有金额没对上列含义的列，提示审核员指定列含义。 */
export function unmappedColumns(table: CorrectionTable): Set<number> {
  return new Set(
    (table.unresolved_money_cells ?? [])
      .filter((cell) => cell.reason === "amount_column_unmapped")
      .map((cell) => cell.column),
  );
}

export function columnMeasure(table: CorrectionTable, column: number): string | null {
  return table.column_mappings?.find((item) => item.source_col_idx === column)?.canonical_measure ?? null;
}

export function measureLabel(table: CorrectionTable, measure: string | null): string {
  if (!measure) return "未对应";
  return table.measure_options?.find((item) => item.measure === measure)?.label ?? measure;
}

function validBox(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length !== 4) return null;
  const box = value.map((item) => Number(item));
  return box.every(Number.isFinite) && box[2] > box[0] && box[3] > box[1] ? box : null;
}

/** 补录证据的原页位置：格自身坐标 → 所在行的原页区域 → 整张表区域。拿不到页码时返回 null。 */
export function cellLocation(table: CorrectionTable, row: number, column: number): CorrectionLocation | null {
  const own = attentionCells(table).get(cellKey(row, column));
  const ownBox = validBox(own?.bbox);
  if (own && ownBox && own.page > 0) return { physical_page: own.page, bbox: ownBox };
  const rowBox = table.row_boxes?.[row];
  const rowBbox = validBox(rowBox?.bbox);
  if (rowBox && rowBbox && rowBox.page > 0) return { physical_page: rowBox.page, bbox: rowBbox };
  const tableBox = validBox(table.bbox);
  return tableBox && table.page > 0 ? { physical_page: table.page, bbox: tableBox } : null;
}

export function gapsForTable(targets: CorrectionTargets, table: CorrectionTable): ReadingGap[] {
  return (targets.gaps ?? []).filter((gap) => gap.affected_fragment_uids?.includes(table.fragment_uid));
}

export type PendingCorrection =
  | {
      kind: "cell";
      tableUid: string;
      tableTitle: string;
      row: number;
      column: number;
      previous: string;
      value: string;
      location: CorrectionLocation;
    }
  | {
      kind: "column";
      tableUid: string;
      tableTitle: string;
      column: number;
      measure: string;
      measureLabel: string;
      location: CorrectionLocation;
    }
  | {
      kind: "rows";
      tableUid: string;
      tableTitle: string;
      mode: "append_rows" | "replace_data_rows";
      rows: { cells: Record<number, string> }[];
      location: CorrectionLocation;
    }
  | {
      kind: "identity";
      tableUid: string;
      tableTitle: string;
      tableCode: string;
      tableCodeLabel: string;
      originalHeaderMissing: boolean;
      location: CorrectionLocation;
    };

export function pendingKey(item: PendingCorrection): string {
  if (item.kind === "rows") return `rows|${item.tableUid}|${item.mode}`;
  if (item.kind === "identity") return `identity|${item.tableUid}`;
  return item.kind === "cell"
    ? `cell|${item.tableUid}|${item.row}|${item.column}`
    : `column|${item.tableUid}|${item.column}`;
}

/** 同一格/同一列/同一表的同一类整行操作只保留最后一次修改。 */
export function upsertPending(list: PendingCorrection[], item: PendingCorrection): PendingCorrection[] {
  const key = pendingKey(item);
  return [...list.filter((existing) => pendingKey(existing) !== key), item];
}

export function describePending(item: PendingCorrection): string {
  if (item.kind === "column") {
    return `《${item.tableTitle}》第 ${item.column + 1} 列 → ${item.measureLabel}`;
  }
  if (item.kind === "identity") {
    const header = item.originalHeaderMissing ? "；原件缺表头，只确认缺失" : "";
    return `《${item.tableTitle}》确认表种 → ${item.tableCodeLabel}${header}`;
  }
  if (item.kind === "rows") {
    const action = item.mode === "append_rows" ? "新增" : "替换数据行";
    return `《${item.tableTitle}》${action} ${item.rows.length} 行`;
  }
  const before = item.previous.trim() ? `“${item.previous.trim()}”` : "空白";
  return `《${item.tableTitle}》第 ${item.row + 1} 行第 ${item.column + 1} 列：${before} → “${item.value.trim()}”`;
}

export function toCorrectionChanges(list: PendingCorrection[]): Record<string, unknown>[] {
  return list.map((item) => {
    if (item.kind === "identity") {
      return {
        target_type: "table_instance",
        target_id: item.tableUid,
        field: "table_identity",
        value: { table_code: item.tableCode, original_header_missing: item.originalHeaderMissing },
        location: item.location,
      };
    }
    if (item.kind === "rows") {
      return {
        target_type: "table_instance",
        target_id: item.tableUid,
        field: "table_rows",
        value: { mode: item.mode, rows: item.rows },
        location: item.location,
      };
    }
    return item.kind === "cell"
      ? {
          target_type: "table_instance",
          target_id: item.tableUid,
          field: "cell_value",
          value: { row_idx: item.row, col_idx: item.column, raw_value: item.value.trim() },
          location: item.location,
        }
      : {
          target_type: "table_instance",
          target_id: item.tableUid,
          field: "column_mapping",
          value: { mappings: [{ canonical_measure: item.measure, source_col_idx: item.column }] },
          location: item.location,
        };
  });
}

const AMOUNT_RE = /^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$/;

/** 解析器未定表种的编码：`UNKNOWN` 或 `UNKNOWN_<页>_T<序号>`（真实产物是后者）。 */
export function isUnknownTableCode(code: string | null | undefined): boolean {
  const text = (code ?? "").trim();
  return !text || text === "UNKNOWN" || text.startsWith("UNKNOWN_");
}

export function needsTableIdentity(table: CorrectionTable): boolean {
  return isUnknownTableCode(table.table_code);
}

/** 整表/整行草稿：每行一条，列之间用 | 或制表符分隔；空行忽略。 */
export function parseDraftRows(text: string, columnCount: number): { rows: { cells: Record<number, string> }[]; error: string | null } {
  const rows: { cells: Record<number, string> }[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const values = line.split(/\t|\|/).map((value) => value.trim());
    if (values.length > columnCount) {
      return { rows: [], error: `每行最多 ${columnCount} 列，当前有 ${values.length} 列` };
    }
    const cells: Record<number, string> = {};
    values.forEach((value, column) => { if (value) cells[column] = value; });
    if (Object.keys(cells).length === 0) continue;
    rows.push({ cells });
  }
  if (rows.length === 0) return { rows: [], error: "请按原页填写至少一行" };
  return { rows, error: null };
}

/** 整行补录的数字列校验与单格补录同口径。 */
export function validateDraftRows(
  rows: { cells: Record<number, string> }[],
  amountColumns: Set<number>,
  knownAmountColumns: Set<number>,
): string | null {
  for (const row of rows) {
    for (const [column, value] of Object.entries(row.cells)) {
      const index = Number(column);
      const isAmount = amountColumns.has(index) || knownAmountColumns.has(index);
      const problem = validateCellValue(value, isAmount);
      if (problem) return `第 ${index + 1} 列：${problem}`;
    }
  }
  return null;
}

export function tableLocation(table: CorrectionTable): CorrectionLocation | null {
  const box = validBox(table.bbox);
  return box && table.page > 0 ? { physical_page: table.page, bbox: box } : null;
}

/** 数据行里出现过数字的列也按金额列校验，避免把文字填进未映射的金额列。 */
export function knownAmountColumns(table: CorrectionTable): Set<number> {
  const result = new Set(table.column_mappings.map((item) => item.source_col_idx));
  const headerCount = Math.max(0, Math.min(table.header_row_count ?? 0, table.rows.length));
  for (const row of table.rows.slice(headerCount)) {
    row.forEach((value, column) => {
      // 前两列是科目编码/名称，编码本身也是数字，不能据此当成金额列。
      if (column >= 2 && AMOUNT_RE.test(String(value ?? "").trim().replace(/\s+/g, ""))) result.add(column);
    });
  }
  return result;
}

/** 金额列只能填数字（与后端校验同口径）；其他列只要求非空且不超过 100 字。 */
export function validateCellValue(value: string, isAmountColumn: boolean): string | null {
  const text = value.trim();
  if (!text) return "请填写原页上的内容";
  if (text.length > 100) return "内容不能超过 100 字";
  if (isAmountColumn && !AMOUNT_RE.test(text.replace(/\s+/g, ""))) return "金额列只能填写数字，例如 1,234.56";
  return null;
}

/** 提交失败时给出业务原因，而不是只显示状态码。 */
export function describeSubmitError(status: number, detail: unknown): string {
  const record = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : {};
  const inner = record.detail && typeof record.detail === "object" ? (record.detail as Record<string, unknown>) : null;
  if (status === 409) return String(inner?.message ?? "分析版本已更新，请刷新后重新提交");
  if (typeof record.detail === "string") return record.detail;
  if (inner?.message) return String(inner.message);
  if (Array.isArray(record.detail) && record.detail.length > 0) {
    const first = record.detail[0] as Record<string, unknown>;
    return `提交内容不符合要求：${String(first?.msg ?? "格式错误")}`;
  }
  return `提交失败（HTTP ${status}）`;
}

export function countCorrectionTargets(targets: CorrectionTargets | null): number {
  if (!targets) return 0;
  const tableIds = new Set(targets.tables.map((table) => table.fragment_uid));
  const looseGaps = targets.gaps.filter(
    (gap) => !(gap.affected_fragment_uids ?? []).some((uid) => tableIds.has(uid)),
  );
  return targets.tables.length + looseGaps.length;
}
