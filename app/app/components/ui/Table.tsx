import type { ThHTMLAttributes, TdHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Table 系列：原型图处理队列表、质量管理发布门禁列表等表格的统一单元格样式。
 * 表头浅灰底 + 大写字距字母排布（对照原型图"文档""当前阶段""质量状态"等表头样式），
 * 单元格保持左对齐、纵向居中，行 hover 时浅灰底提示可交互。
 */
export function Th({ className, children, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-border bg-surface-100 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500",
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  );
}

export function Td({ className, children, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("border-b border-border px-4 py-3 text-sm text-slate-700", className)} {...rest}>
      {children}
    </td>
  );
}

const TableCells = { Th, Td };

export default TableCells;
