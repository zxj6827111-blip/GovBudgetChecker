"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui";

import {
  attentionCells,
  cellKey,
  cellLocation,
  columnMeasure,
  describeGap,
  describePending,
  describeSubmitError,
  gapsForTable,
  measureLabel,
  pendingKey,
  toCorrectionChanges,
  unmappedColumns,
  upsertPending,
  validateCellValue,
  type CorrectionLocation,
  type CorrectionTable,
  type CorrectionTargets,
  type PendingCorrection,
  type ReadingGap,
} from "./correctionAdapters";

/**
 * 「读取校正」页签：系统读不全的表格，由审核员对照左侧原页补录、确认或指定列含义。
 *
 * 纪律：
 * 1. 只改系统报出来的表；每条校正都带原页位置，服务端校验页码与坐标后生成人工证据；
 * 2. 校正以追加记录保存，原始解析结果不覆盖；提交后自动重新分析，规则按校正后的数据重跑；
 * 3. 补录的数据在问题清单里标注"含人工补录数据"，不冒充系统读取结果。
 */

export interface CorrectionFocus {
  page: number;
  bbox: number[] | null;
  label: string;
}

interface CorrectionsTabProps {
  jobId: string;
  /** 正在重新分析时禁止提交，避免基于旧版本校正。 */
  disabled: boolean;
  onFocus: (focus: CorrectionFocus | null) => void;
  onSubmitted: () => void;
}

interface SelectedCell {
  tableUid: string;
  row: number;
  column: number;
}

function TableCard({
  table,
  gaps,
  pending,
  selected,
  disabled,
  onSelectCell,
  onLocate,
  onQueue,
}: {
  table: CorrectionTable;
  gaps: ReadingGap[];
  pending: PendingCorrection[];
  selected: SelectedCell | null;
  disabled: boolean;
  onSelectCell: (cell: SelectedCell) => void;
  onLocate: (focus: CorrectionFocus) => void;
  onQueue: (item: PendingCorrection) => void;
}) {
  const attention = useMemo(() => attentionCells(table), [table]);
  const unmapped = useMemo(() => unmappedColumns(table), [table]);
  const headerCount = Math.max(0, Math.min(table.header_row_count ?? 1, table.rows.length));
  const columnCount = Math.max(0, ...table.rows.map((row) => row.length));
  const pendingByKey = new Map(pending.filter((item) => item.tableUid === table.fragment_uid).map((item) => [pendingKey(item), item]));
  const isSelected = selected?.tableUid === table.fragment_uid;
  const selectedRow = isSelected ? selected.row : -1;
  const selectedColumn = isSelected ? selected.column : -1;
  const selectedAttention = isSelected ? attention.get(cellKey(selectedRow, selectedColumn)) : undefined;
  const selectedPending = isSelected ? pendingByKey.get(`cell|${table.fragment_uid}|${selectedRow}|${selectedColumn}`) : undefined;
  const currentText = isSelected ? String(table.rows[selectedRow]?.[selectedColumn] ?? "") : "";
  const [draft, setDraft] = useState("");
  const [draftLocation, setDraftLocation] = useState<CorrectionLocation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hasMappings = table.measure_options.length > 0;
  const amountColumns = new Set(table.column_mappings.map((item) => item.source_col_idx));

  useEffect(() => {
    setDraft(selectedPending && selectedPending.kind === "cell" ? selectedPending.value : currentText);
    setDraftLocation(null);
    setError(null);
    // 只在切换选中格时重置输入。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRow, selectedColumn, table.fragment_uid]);

  const queueCell = (value: string) => {
    const isAmount = amountColumns.has(selectedColumn) || unmapped.has(selectedColumn);
    const problem = validateCellValue(value, isAmount);
    if (problem) {
      setError(problem);
      return;
    }
    const location = draftLocation ?? cellLocation(table, selectedRow, selectedColumn);
    if (!location) {
      setError("这一格拿不到原页位置，无法作为证据提交");
      return;
    }
    onQueue({
      kind: "cell",
      tableUid: table.fragment_uid,
      tableTitle: table.title || table.table_code,
      row: selectedRow,
      column: selectedColumn,
      previous: currentText,
      value,
      location,
    });
    setError(null);
  };

  const queueColumn = (column: number, measure: string) => {
    const location = cellLocation(table, headerCount, column) ?? cellLocation(table, 0, column);
    if (!location) return;
    onQueue({
      kind: "column",
      tableUid: table.fragment_uid,
      tableTitle: table.title || table.table_code,
      column,
      measure,
      measureLabel: measureLabel(table, measure),
      location,
    });
  };

  return (
    <section className="border-b border-border px-4 py-3" data-testid={`gbc-correction-table-${table.fragment_uid}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-slate-800">《{table.title || table.table_code}》</span>
        <span className="text-slate-500">第 {table.page} 页</span>
        {attention.size > 0 ? (
          <span className="rounded-full bg-warning-100 px-2 py-0.5 text-warning-700">待处理 {attention.size} 格</span>
        ) : null}
        {unmapped.size > 0 ? (
          <span className="rounded-full bg-warning-100 px-2 py-0.5 text-warning-700">{unmapped.size} 列未对应列含义</span>
        ) : null}
        {table.arithmetic_conflicts?.length ? (
          <span className="rounded-full bg-danger-100 px-2 py-0.5 text-danger-700">合计与分项对不上，列可能错位</span>
        ) : null}
        <button
          type="button"
          className="ml-auto text-primary-600 hover:text-primary-700"
          onClick={() => onLocate({ page: table.page, bbox: table.bbox, label: `《${table.title || table.table_code}》` })}
        >
          定位原页
        </button>
      </div>

      {gaps.length > 0 ? (
        <ul className="mb-2 space-y-1 text-xs text-slate-600">
          {gaps.map((gap) => (
            <li key={gap.id} className="flex flex-wrap items-center gap-2 rounded bg-surface-100 px-2 py-1">
              <span>第 {gap.page} 页：{describeGap(gap)}</span>
              <button
                type="button"
                className="text-primary-600 hover:text-primary-700"
                onClick={() => onLocate({ page: gap.page, bbox: gap.bbox, label: "未读区域" })}
              >
                定位
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="max-h-96 overflow-auto rounded border border-border">
        <table className="min-w-full border-collapse text-[11px]">
          <tbody>
            {hasMappings ? (
              <tr className="bg-surface-100">
                {Array.from({ length: columnCount }, (_, column) => {
                  const mapped = columnMeasure(table, column);
                  const pendingColumn = pendingByKey.get(`column|${table.fragment_uid}|${column}`);
                  const value = pendingColumn && pendingColumn.kind === "column" ? pendingColumn.measure : mapped ?? "";
                  if (!mapped && !unmapped.has(column)) {
                    return <td key={column} className="border border-border px-1 py-1" />;
                  }
                  return (
                    <td key={column} className={`border border-border px-1 py-1 ${unmapped.has(column) && !pendingColumn ? "bg-warning-50" : ""}`}>
                      <select
                        aria-label={`第 ${column + 1} 列含义`}
                        className="w-full rounded border border-border bg-white px-1 py-0.5 text-[11px]"
                        value={value}
                        disabled={disabled}
                        onChange={(event) => event.target.value && queueColumn(column, event.target.value)}
                      >
                        <option value="">未对应</option>
                        {table.measure_options.map((option) => (
                          <option key={option.measure} value={option.measure}>{option.label}</option>
                        ))}
                      </select>
                    </td>
                  );
                })}
              </tr>
            ) : null}
            {table.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className={rowIndex < headerCount ? "bg-surface-100 font-medium text-slate-700" : ""}>
                {Array.from({ length: columnCount }, (_, column) => {
                  const key = cellKey(rowIndex, column);
                  const flagged = attention.get(key);
                  const queued = pendingByKey.get(`cell|${table.fragment_uid}|${rowIndex}|${column}`);
                  const active = rowIndex === selectedRow && column === selectedColumn;
                  const text = queued && queued.kind === "cell" ? queued.value : String(row[column] ?? "");
                  const tone = queued ? "bg-primary-50 text-primary-700" : flagged ? "bg-warning-50 text-warning-700" : "";
                  return (
                    <td
                      key={column}
                      title={flagged?.label}
                      onClick={() => rowIndex >= headerCount && onSelectCell({ tableUid: table.fragment_uid, row: rowIndex, column })}
                      className={`whitespace-nowrap border border-border px-1.5 py-1 ${tone} ${
                        rowIndex >= headerCount ? "cursor-pointer hover:bg-primary-50" : ""
                      } ${active ? "outline outline-2 outline-primary-500" : ""}`}
                    >
                      {text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {isSelected ? (
        <div className="mt-2 space-y-2 rounded border border-primary-200 bg-primary-50/40 p-2 text-xs" data-testid="gbc-correction-cell-editor">
          <div className="text-slate-700">
            第 {selectedRow + 1} 行第 {selectedColumn + 1} 列
            {selectedAttention ? <span className="ml-2 text-warning-700">{selectedAttention.label}</span> : null}
            {(() => {
              const location = cellLocation(table, selectedRow, selectedColumn);
              return location ? <span className="ml-2 text-slate-500">原页第 {location.physical_page} 页</span> : null;
            })()}
          </div>
          {gaps.some((gap) => gap.raw_text) ? (
            <div className="flex flex-wrap gap-1">
              {gaps.filter((gap) => gap.raw_text).map((gap) => (
                <button
                  key={gap.id}
                  type="button"
                  className="rounded border border-info-200 bg-info-50 px-2 py-0.5 text-info-700 hover:bg-info-100"
                  onClick={() => {
                    setDraft(String(gap.raw_text));
                    if (gap.bbox && gap.page > 0) setDraftLocation({ physical_page: gap.page, bbox: gap.bbox });
                  }}
                >
                  填入原页未读金额 {gap.raw_text}
                </button>
              ))}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            <input
              aria-label="原页上的内容"
              className="min-w-0 flex-1 rounded border border-border bg-white px-2 py-1"
              value={draft}
              disabled={disabled}
              placeholder="照原页填写"
              onChange={(event) => setDraft(event.target.value)}
            />
            {selectedAttention?.kind === "scan" && currentText.trim() ? (
              <Button size="sm" variant="secondary" disabled={disabled} onClick={() => queueCell(currentText)}>
                与原页一致
              </Button>
            ) : null}
            <Button size="sm" disabled={disabled} onClick={() => queueCell(draft)}>
              加入待提交
            </Button>
          </div>
          {error ? <div className="text-danger-700">{error}</div> : null}
        </div>
      ) : null}
    </section>
  );
}

export function CorrectionsTab({ jobId, disabled, onFocus, onSubmitted }: CorrectionsTabProps) {
  const [targets, setTargets] = useState<CorrectionTargets | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCorrection[]>([]);
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/correction-targets`, { cache: "no-store" });
      if (!response.ok) {
        setLoadError(describeSubmitError(response.status, await response.json().catch(() => ({}))));
        return;
      }
      setTargets((await response.json()) as CorrectionTargets);
    } catch {
      setLoadError("校正数据暂时无法读取");
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectCell = (cell: SelectedCell) => {
    setSelected(cell);
    const table = targets?.tables.find((item) => item.fragment_uid === cell.tableUid);
    const location = table ? cellLocation(table, cell.row, cell.column) : null;
    onFocus(location ? { page: location.physical_page, bbox: location.bbox, label: "待校正格" } : null);
  };

  const submit = async () => {
    if (!targets?.analysis_basis_token || pending.length === 0) return;
    if (reason.trim().length < 3) {
      setMessage({ tone: "error", text: "请填写校正说明（至少 3 个字），例如“对照原页第 8 页补录扫描件未读金额”" });
      return;
    }
    setSubmitting(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/corrections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          analysis_basis_token: targets.analysis_basis_token,
          changes: toCorrectionChanges(pending),
          reason: reason.trim(),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMessage({ tone: "error", text: describeSubmitError(response.status, body) });
        if (response.status === 409) void load();
        return;
      }
      setPending([]);
      setReason("");
      setSelected(null);
      onFocus(null);
      setMessage({ tone: "ok", text: String((body as Record<string, unknown>).message ?? "校正已提交，正在重新分析") });
      onSubmitted();
    } catch {
      setMessage({ tone: "error", text: "校正提交失败，请稍后重试" });
    } finally {
      setSubmitting(false);
    }
  };

  if (loadError) {
    return (
      <div className="space-y-2 p-4 text-sm text-slate-600">
        <p>{loadError}</p>
        <Button size="sm" variant="secondary" onClick={() => void load()}>重新读取</Button>
      </div>
    );
  }
  if (!targets) {
    return <div className="p-4 text-sm text-slate-500">正在读取需要校正的表格…</div>;
  }

  const tableIds = new Set(targets.tables.map((table) => table.fragment_uid));
  const looseGaps = targets.gaps.filter((gap) => !(gap.affected_fragment_uids ?? []).some((uid) => tableIds.has(uid)));
  const overlay = targets.overlay_status;
  const overlayProblems = overlay && (overlay.status === "partial" || overlay.status === "failed") ? overlay.unresolved ?? [] : [];

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="gbc-review-corrections-tab">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <p className="border-b border-border px-4 py-2 text-xs leading-relaxed text-slate-500">
          对照左侧原页：点击表格中的格子补录或确认内容，或在表头上方指定金额列的含义。提交后自动重新分析；原始读取结果保留，
          用到补录数据的问题会标注“含人工补录数据”。
        </p>
        {overlayProblems.length > 0 ? (
          <div className="mx-4 mt-2 rounded border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
            上次校正有 {overlayProblems.length} 条没有生效：
            <ul className="mt-1 list-disc pl-4">
              {overlayProblems.slice(0, 5).map((item, index) => <li key={index}>{String(item.reason ?? "原因未说明")}</li>)}
            </ul>
          </div>
        ) : null}
        {targets.tables.length === 0 && looseGaps.length === 0 ? (
          <p className="px-4 py-6 text-sm text-slate-500">当前没有需要人工补录或校正的表格。</p>
        ) : null}
        {looseGaps.length > 0 ? (
          <section className="border-b border-border px-4 py-3 text-xs">
            <div className="mb-1 font-semibold text-slate-800">原页上没有读成表格的区域</div>
            <p className="mb-2 text-slate-500">这些区域没有可补录的表格结构。请先核对原页；如为扫描件，建议取得文字版 PDF 后重新上传。</p>
            <ul className="space-y-1">
              {looseGaps.map((gap) => (
                <li key={gap.id} className="flex flex-wrap items-center gap-2 rounded bg-surface-100 px-2 py-1 text-slate-600">
                  <span>第 {gap.page} 页：{describeGap(gap)}</span>
                  <button
                    type="button"
                    className="text-primary-600 hover:text-primary-700"
                    onClick={() => onFocus({ page: gap.page, bbox: gap.bbox, label: "未读区域" })}
                  >
                    定位
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {targets.tables.map((table) => (
          <TableCard
            key={table.fragment_uid}
            table={table}
            gaps={gapsForTable(targets, table)}
            pending={pending}
            selected={selected}
            disabled={disabled || submitting}
            onSelectCell={selectCell}
            onLocate={onFocus}
            onQueue={(item) => setPending((list) => upsertPending(list, item))}
          />
        ))}
        {targets.history.length > 0 ? (
          <section className="px-4 py-3 text-xs">
            <button type="button" className="font-medium text-slate-600 hover:text-slate-800" onClick={() => setShowHistory((value) => !value)}>
              {showHistory ? "收起" : "查看"}以往校正记录（{targets.history.length}）
            </button>
            {showHistory ? (
              <ul className="mt-2 space-y-1 text-slate-600">
                {[...targets.history].reverse().map((record) => (
                  <li key={record.record_id} className="rounded bg-surface-100 px-2 py-1">
                    {record.created_at?.slice(0, 16).replace("T", " ")} · {record.actor} · {record.changes.length} 项 · {record.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        ) : null}
      </div>

      {pending.length > 0 || message ? (
        <div className="shrink-0 space-y-2 border-t border-border bg-white px-4 py-3 text-xs" data-testid="gbc-correction-pending">
          {pending.length > 0 ? (
            <>
              <div className="font-semibold text-slate-800">待提交（{pending.length}）</div>
              <ul className="max-h-28 space-y-1 overflow-y-auto">
                {pending.map((item) => (
                  <li key={pendingKey(item)} className="flex items-center gap-2 text-slate-600">
                    <span className="min-w-0 flex-1 truncate" title={describePending(item)}>{describePending(item)}</span>
                    <button
                      type="button"
                      className="text-slate-400 hover:text-danger-600"
                      onClick={() => setPending((list) => list.filter((other) => pendingKey(other) !== pendingKey(item)))}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
              <textarea
                aria-label="校正说明"
                className="w-full rounded border border-border px-2 py-1"
                rows={2}
                value={reason}
                placeholder="校正说明（必填），例如：对照原页第 8 页补录扫描件未读金额"
                onChange={(event) => setReason(event.target.value)}
              />
              <Button size="sm" disabled={disabled || submitting || !targets.analysis_basis_token} onClick={() => void submit()}>
                {submitting ? "提交中…" : `提交校正并重新分析（${pending.length}）`}
              </Button>
            </>
          ) : null}
          {message ? (
            <div className={message.tone === "ok" ? "text-success-700" : "text-danger-700"}>{message.text}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
