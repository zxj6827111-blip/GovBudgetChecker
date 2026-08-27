/** Metric 的纯逻辑层：tone -> className 映射 + 空值判定。拆分原因见 buttonStyles.ts 顶部注释。 */

export type MetricTone = "primary" | "success" | "warning" | "danger" | "info" | "neutral";

export const METRIC_TONE_VALUE_CLASSES: Record<MetricTone, string> = {
  primary: "text-primary-700",
  success: "text-success-700",
  warning: "text-warning-700",
  danger: "text-danger-700",
  info: "text-info-700",
  neutral: "text-slate-900",
};

/**
 * 判定 Metric 的 value 是否属于"未知/暂无数据"语义。
 *
 * 关键反例（M1 不制造虚假成功原则的延续）：
 * - null / undefined / "" 均视为空，渲染为 "—"；
 * - 数字 0 不是空值，是一个合法的真实数据点（例如"处理失败：0"表示"已确认为零"），
 *   必须原样显示为 0，绝不能被这个函数误判为空后又被上层渲染成 "—" 或被猜测成别的数字。
 */
export function isMetricValueEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}
