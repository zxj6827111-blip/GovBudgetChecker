import type { ExpenditureContext } from "../../../lib/mock";

const LABELS: Record<string, string> = {
  total_actual: "本年支出合计", basic_actual: "基本支出", project_actual: "项目支出",
  to_superior_actual: "上缴上级支出", operational_actual: "经营支出", subsidiary_actual: "对附属单位补助",
};
const amount = (value: string) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 同一原行三列并排；空白零与相邻项目金额同时可见，各列分别加总。 */
export function ExpenditureContextTable({ context, selectedKey, onSelect }: {
  context: ExpenditureContext; selectedKey?: string | null; onSelect?: (key: string) => void;
}) {
  const columns = context.columns.filter((column, index) => index < 3 ||
    context.rows.some((row) => Number(row.cells.find((cell) => cell.indicator === column.indicator)?.value ?? 0) !== 0));
  const hiddenZeroColumns = context.columns.length - columns.length;
  return (
    <details open className="mb-5 border-y border-border" data-testid="expenditure-column-reconciliation">
      <summary className="cursor-pointer py-2.5 text-[0.85em] font-medium text-primary-700">
        支出三列逐类复算 · {context.rows.length - 1}个类科目
      </summary>
      <p className="mb-2 text-[0.82em] text-slate-600">
        当前校验列：{LABELS[context.checkedIndicator] ?? "支出金额"}。每列单独向下加总；每行核对本年支出是否等于各分项之和。“空白”按0参与所在列，相邻项目金额另列保留。
        {hiddenZeroColumns > 0 ? `其余${hiddenZeroColumns}项均为原件空白或明确零，已纳入行勾稽。` : null}
      </p>
      <div className="overflow-x-auto">
        <table className="mb-2 w-full border-collapse text-[0.8em]" aria-label="本年支出、基本支出和项目支出并排复算，金额单位万元">
          <thead><tr className="text-slate-500">
            <th className="px-1 py-2 text-left font-normal">科目</th>
            {columns.map((column) => <th key={column.indicator} className={`px-1 py-2 text-right font-normal ${column.indicator === context.checkedIndicator ? "bg-primary-50" : ""}`}>{LABELS[column.indicator]}（万元）</th>)}
            <th className="px-1 py-2 text-right font-normal">行内核对</th>
          </tr></thead>
          <tbody>{context.rows.map((row) => <tr key={row.code} className={`border-t border-border ${row.code === "total" ? "font-medium" : ""}`}>
            <td className="px-1 py-2">{row.code === "total" ? "表内总计" : `${row.code} ${row.name}`}</td>
            {columns.map((column) => {
              const cell = row.cells.find((item) => item.indicator === column.indicator)!;
              return <td key={column.indicator} className={`px-1 py-2 text-right tabular-nums ${cell.key === selectedKey ? "bg-primary-100" : column.indicator === context.checkedIndicator ? "bg-primary-50" : ""}`}>
                <button type="button" onClick={() => onSelect?.(cell.key)} className="text-primary-700 hover:underline"
                  aria-label={`定位${row.code} ${LABELS[column.indicator]} ${amount(cell.value)}万元，第${cell.page}页`}>
                  {amount(cell.value)}{cell.blankAsZero ? <span className="ml-1 text-[0.85em] text-slate-500">（空白）</span> : null}
                </button>
              </td>;
            })}
            <td className={`px-1 py-2 text-right ${row.rowStatus === "mismatch" ? "text-danger-700" : "text-slate-500"}`}>
              {row.rowStatus === "mismatch" ? `差${amount(row.rowDifference)}` : row.rowStatus === "rounding_hint" ? `舍入差${amount(row.rowDifference)}` : "相符"}
            </td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="space-y-1 pb-3 text-[0.83em]" aria-label="各列合计核对结果">
        {context.columns.slice(0, 3).map((column) => <p key={column.indicator} className={column.status === "mismatch" ? "text-danger-700" : "text-slate-600"}>
          {LABELS[column.indicator]}：各类合计{amount(column.computed)}，表内总计{amount(column.actual)}，
          {column.status === "mismatch" ? `相差${amount(column.difference)}万元` : column.status === "rounding_hint" ? `舍入差${amount(column.difference)}万元` : "相符"}。
        </p>)}
      </div>
    </details>
  );
}
