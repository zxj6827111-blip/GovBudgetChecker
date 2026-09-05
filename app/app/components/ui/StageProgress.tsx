import { cn } from "@/lib/utils";

import {
  clampProgress,
  formatProgressText,
  isProgressUnknown,
  STAGE_PROGRESS_TONE_FILL_CLASSES,
  type StageProgressTone,
} from "./stageProgressStyles";

/**
 * StageProgress：原型图处理队列表「当前阶段」列的阶段名 + 百分比进度条。
 *
 * 严禁事项（对照任务书"禁止渲染任何未经度量的数字"与 M1 不制造虚假成功原则）：
 * - progress 为 null/undefined 时必须渲染 "—"，绝不能渲染 0% 或任何猜测值。
 *   0% 与"未知"是两个完全不同的语义：0% 意味着"已确认还没开始"，
 *   未知意味着"系统当前无法判断进度"（例如 Task 3 尚未产出该阶段的完成度）。
 *   把 null 显示成 0% 会让用户误以为系统"卡在 0%"，属于虚假信息。
 * - progress 为数字时会做 [0, 100] 边界夹紧，防止上游传入越界值时进度条溢出容器。
 *
 * tone 用于进度条填充色，对照原型图不同任务的进度条颜色差异
 * （92%/68% 为主色 primary，54% 为 warning，处理失败为 danger）。
 *
 * 未知态判定 / 夹紧 / 文案格式化逻辑抽在 stageProgressStyles.ts，原因见
 * buttonStyles.ts 顶部注释——这是本组件库语义最敏感的一段判定逻辑，
 * 单独抽出后可用现有 jiti 测试脚本直接对纯函数做单测。
 */
export type { StageProgressTone };

export interface StageProgressProps {
  stageLabel: string;
  progress: number | null | undefined;
  tone?: StageProgressTone;
  className?: string;
  /** 可选测试锚点：供 e2e 精确定位某一行进度条，不影响样式与业务逻辑。 */
  "data-testid"?: string;
}

export function StageProgress({
  stageLabel,
  progress,
  tone = "primary",
  className,
  "data-testid": testId,
}: StageProgressProps) {
  const isUnknown = isProgressUnknown(progress);
  const clamped = isUnknown ? 0 : clampProgress(progress as number);
  const displayText = formatProgressText(progress);

  return (
    <div className={cn("min-w-[160px]", className)} data-testid={testId}>
      <div className="flex items-center justify-between text-xs text-slate-600">
        <span className="truncate font-medium">{stageLabel}</span>
        <span
          className={cn("ml-2 shrink-0 font-semibold", isUnknown ? "text-slate-400" : "text-slate-700")}
          aria-label={isUnknown ? "进度未知" : `进度 ${Math.round(clamped)}%`}
        >
          {displayText}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={isUnknown ? undefined : Math.round(clamped)}
        aria-valuetext={isUnknown ? "进度未知" : `${Math.round(clamped)}%`}
      >
        {isUnknown ? null : (
          <div
            className={cn("h-full rounded-full transition-all", STAGE_PROGRESS_TONE_FILL_CLASSES[tone])}
            style={{ width: `${clamped}%` }}
          />
        )}
      </div>
    </div>
  );
}

export default StageProgress;
