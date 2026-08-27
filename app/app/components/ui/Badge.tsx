import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { BADGE_BASE_CLASSES, BADGE_TONE_CLASSES, type BadgeTone } from "./badgeStyles";

/**
 * Badge：原型图处理队列表「质量状态」列出现的全部徽章状态，逐一对照原型图取色
 * （见 docs/UI_COLOR_TOKEN_MAPPING.md）：
 * - review（需要人工复核）：浅橙底 warning-100 + warning-700 文字；
 * - processing（正在分析）：浅青底 primary-100 + primary-700 文字；
 * - lowconf（低置信度）：浅橙底 warning-100 + warning-700 文字（与 review 同色系，
 *   原型图中二者视觉上确实同色，语义靠文案区分）；
 * - failed（处理失败）：浅红底 danger-100 + danger-700 文字；
 * - done（分析完成/已通过全部质量门禁）：浅绿底 success-100 + success-700 文字。
 *
 * 额外提供 neutral 供无法归入以上任一状态时使用（例如 Task 3 阶段未知时的占位徽章），
 * 不得因为找不到对应 tone 就随意套用 done/success——那会制造虚假的"已完成"印象。
 *
 * tone -> className 映射表抽在 badgeStyles.ts，原因见 buttonStyles.ts 顶部注释。
 */
export type { BadgeTone };

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}

export function Badge({ tone = "neutral", children, className }: BadgeProps) {
  return <span className={cn(BADGE_BASE_CLASSES, BADGE_TONE_CLASSES[tone], className)}>{children}</span>;
}

export default Badge;
