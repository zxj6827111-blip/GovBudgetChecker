import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Card：原型图中 KPI 卡、处理队列表、质量告警面板、最近活动面板等区块的统一外壳。
 * 对照原型图取值（见 docs/UI_COLOR_TOKEN_MAPPING.md）：
 * - 卡片背景白色，边框 border（既有 token，未变），圆角与 shadow-soft 对齐原型图卡片的
 *   轻微投影效果（原型图未使用重投影，符合政务系统克制的视觉语言）。
 * - title/desc/action 三段式布局对应原型图「标题 + 说明文字 + 右上角操作按钮」的通用结构
 *   （如「处理队列」标题 + 说明 + 「查看全部任务→」链接）。
 */
export interface CardProps {
  title?: ReactNode;
  desc?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** 可选测试锚点：供 e2e/单测定位某个卡片区块，不影响样式与业务逻辑（Task 4 起新增）。 */
  "data-testid"?: string;
}

export function Card({
  title,
  desc,
  action,
  children,
  className,
  bodyClassName,
  "data-testid": testId,
}: CardProps) {
  const hasHeader = Boolean(title || desc || action);
  return (
    <section className={cn("rounded-card border border-border bg-white shadow-soft", className)} data-testid={testId}>
      {hasHeader ? (
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            {title ? <h3 className="text-sm font-semibold text-slate-900">{title}</h3> : null}
            {desc ? <p className="mt-1 text-xs text-slate-500">{desc}</p> : null}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </header>
      ) : null}
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

export default Card;
