import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { isMetricValueEmpty, METRIC_TONE_VALUE_CLASSES, type MetricTone } from "./metricStyles";

/**
 * Metric：原型图工作台总览顶部 5 张 KPI 卡（待人工复核/正在处理/处理失败/本周已完成/
 * 页面覆盖率）与质量管理页 6 张 KPI 卡的统一形态。
 *
 * 对照原型图结构：label（左上小字）+ 角标（右上，如"今日""24 小时""Gate 1"）+
 * value（大号数字）+ desc（底部灰色辅助说明，如"其中 6 项为高风险问题"）。
 *
 * 重要：value 允许为 null/undefined，此时渲染 "—" 而非猜测值或 0——
 * 这是 M1「不制造虚假成功」原则在 Task 1 组件层的延续（例如页面覆盖率在数据不足时
 * 不得显示 0%，必须显示"—"）。调用方也不应该自行把 null 转换成 0 再传入。
 * 空值判定逻辑抽在 metricStyles.ts，原因见 buttonStyles.ts 顶部注释。
 */
export type { MetricTone };

export interface MetricProps {
  label: ReactNode;
  value: ReactNode | null | undefined;
  desc?: ReactNode;
  tone?: MetricTone;
  corner?: ReactNode;
  className?: string;
  /** 可选测试锚点：供 e2e/单测定位某一张 KPI 卡，不影响样式与业务逻辑（Task 4 起新增）。 */
  "data-testid"?: string;
}

export function Metric({
  label,
  value,
  desc,
  tone = "neutral",
  corner,
  className,
  "data-testid": testId,
}: MetricProps) {
  const isEmpty = isMetricValueEmpty(value);
  return (
    <div className={cn("rounded-card border border-border bg-white p-4 shadow-soft", className)} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-500">{label}</span>
        {corner ? <span className="text-xs text-slate-400">{corner}</span> : null}
      </div>
      <div
        className={cn("mt-2 text-kpi-value", isEmpty ? "text-slate-400" : METRIC_TONE_VALUE_CLASSES[tone])}
        aria-label={isEmpty ? "暂无数据" : undefined}
      >
        {isEmpty ? "—" : value}
      </div>
      {desc ? <p className="mt-1 text-caption text-slate-500">{desc}</p> : null}
    </div>
  );
}

export default Metric;
