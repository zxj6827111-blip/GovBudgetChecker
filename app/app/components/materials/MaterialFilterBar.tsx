"use client";

import type { MaterialStatus } from "@/lib/materialStatusPresentation";
import { MATERIAL_STATUSES, presentMaterialStatus } from "@/lib/materialStatusPresentation";

import {
  FISCAL_YEAR_MAX,
  FISCAL_YEAR_MIN,
  REPORT_KIND_FILTER_OPTIONS,
  normalizeFiscalYearInput,
  type MaterialFilterState,
} from "./materialLedgerAdapters";

/**
 * MaterialFilterBar：三个材料台账页面共用的筛选条（年度 / 文种 / 状态）。
 *
 * 两个刻意如此的设计
 * ------------------
 * 1. **年度不做默认值**。空 = 不按年度筛选；前端找不到任何依据去猜"用户想看哪一年"，
 *    猜一个会让页面看起来有数据、实际却不是那一年。
 * 2. **非法年度不参与查询**，也不被静默改写：显示提示并把上一份结果留在页面上，
 *    比"按 2025 查然后显示 2025 的数据"更不容易被误解。
 */
export interface MaterialFilterBarProps {
  filters: MaterialFilterState;
  onChange: (patch: Partial<MaterialFilterState>) => void;
  /** 状态筛选项是否显示（部门矩阵页不需要）。 */
  showStatus?: boolean;
  /** 追加在筛选条右侧的操作区（如刷新按钮）。 */
  action?: React.ReactNode;
  testIdPrefix: string;
}

const SELECT_CLASSES =
  "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400";

export function MaterialFilterBar({
  filters,
  onChange,
  showStatus = true,
  action,
  testIdPrefix,
}: MaterialFilterBarProps) {
  const year = normalizeFiscalYearInput(filters.fiscalYear);

  return (
    <div className="flex flex-wrap items-start gap-3" data-testid={`${testIdPrefix}-filters`}>
      <div className="flex flex-col gap-1">
        <input
          type="text"
          inputMode="numeric"
          value={filters.fiscalYear}
          onChange={(event) => onChange({ fiscalYear: event.target.value })}
          placeholder="财政年度（如 2025）"
          aria-label="按财政年度筛选"
          data-testid={`${testIdPrefix}-year-filter`}
          className={`${SELECT_CLASSES} w-[190px]`}
        />
        {year.valid ? null : (
          <span className="text-xs text-warning-700" data-testid={`${testIdPrefix}-year-hint`}>
            年度需为 {FISCAL_YEAR_MIN}–{FISCAL_YEAR_MAX} 的 4 位年份，当前输入不参与筛选。
          </span>
        )}
      </div>

      <select
        value={filters.reportKind}
        onChange={(event) =>
          onChange({ reportKind: event.target.value as MaterialFilterState["reportKind"] })
        }
        aria-label="按文种筛选"
        data-testid={`${testIdPrefix}-kind-filter`}
        className={SELECT_CLASSES}
      >
        {REPORT_KIND_FILTER_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      {showStatus ? (
        <select
          value={filters.status}
          onChange={(event) =>
            onChange({ status: event.target.value as MaterialStatus | "all" })
          }
          aria-label="按状态筛选"
          data-testid={`${testIdPrefix}-status-filter`}
          className={SELECT_CLASSES}
        >
          <option value="all">全部状态</option>
          {MATERIAL_STATUSES.map((status) => (
            <option key={status} value={status}>
              {presentMaterialStatus(status).label}
            </option>
          ))}
        </select>
      ) : null}

      {action ? <div className="ml-auto flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

export default MaterialFilterBar;
