/**
 * StageProgress 的纯逻辑层：tone -> className 映射、进度夹紧、未知态判定。
 * 拆分原因见 buttonStyles.ts 顶部注释。
 *
 * 这是本组件库里语义最敏感的一段逻辑，对应任务书"严禁为了填满进度条而伪造百分比"的
 * 核心要求，因此把判定逻辑抽出为可独立单测的纯函数，而不是把这段关键分支埋进 JSX 里
 * 只能靠渲染快照间接验证。
 */

export type StageProgressTone = "primary" | "success" | "warning" | "danger" | "info";

export const STAGE_PROGRESS_TONE_FILL_CLASSES: Record<StageProgressTone, string> = {
  primary: "bg-primary-600",
  success: "bg-success-600",
  warning: "bg-warning-600",
  danger: "bg-danger-600",
  info: "bg-info-600",
};

/**
 * 判定进度值是否属于"未知"语义。
 *
 * 反例（核心断言）：只有 null / undefined 才是"未知"，绝不能把 0 也归入未知——
 * 0 是"已确认还没开始"的真实进度，未知是"系统当前无法判断进度"，
 * 两者语义完全不同，混淆会让用户误以为系统卡住。
 */
export function isProgressUnknown(progress: number | null | undefined): boolean {
  return progress === null || progress === undefined;
}

/**
 * 把进度值夹紧到 [0, 100] 区间，防止越界值把进度条撑破容器。
 * NaN 视为 0（调用方应先用 isProgressUnknown 排除 null/undefined，
 * 这里的 NaN 分支只是兜底异常输入，不应被正常业务路径触发）。
 */
export function clampProgress(value: number): number {
  if (Number.isNaN(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
}

/** 计算展示用文本：未知态显示 em dash，已知态显示四舍五入后的整数百分比。 */
export function formatProgressText(progress: number | null | undefined): string {
  if (isProgressUnknown(progress)) {
    return "—";
  }
  return `${Math.round(clampProgress(progress as number))}%`;
}
