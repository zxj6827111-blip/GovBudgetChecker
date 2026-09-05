import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * SectionTitle：原型图页面主标题 + 一行说明文字的组合（如"工作台总览" + "集中查看
 * 处理队列、需人工复核任务和系统质量告警。"），配右侧操作区（如"导出本页""上传 PDF"）。
 */
export interface SectionTitleProps {
  title: ReactNode;
  desc?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function SectionTitle({ title, desc, action, className }: SectionTitleProps) {
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
        {desc ? <p className="mt-1 text-sm text-slate-500">{desc}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}

export default SectionTitle;
