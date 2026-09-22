"use client";

import { presentStatusReason } from "@/lib/materialStatusPresentation";

/**
 * MaterialStatusReason：状态成因的统一渲染。
 *
 * 为什么它必须和状态徽章一起出现（§八.9）
 * ----------------------------------------
 * `mapping_required` 这类状态本身只说明"要人工确认"，看不出**确认什么**：
 * 可能是主体认不出来、年份认不出来，也可能是两次识别出的口径互相矛盾。
 * 只给一个黄色"异常"徽章，用户只能去翻日志。因此原因码必须紧跟状态显示。
 *
 * 未知原因码原样显示（`待确认：<code>`），不显示成空——空字符串会让
 * "后端给了一个前端不认识的原因"这件事彻底消失。
 */
export interface MaterialStatusReasonProps {
  reason: unknown;
  className?: string;
  testId?: string;
}

export function MaterialStatusReason({ reason, className, testId }: MaterialStatusReasonProps) {
  const label = presentStatusReason(reason);
  if (!label) {
    return null;
  }
  return (
    <span className={className} data-testid={testId} data-status-reason={String(reason)}>
      {label}
    </span>
  );
}

export default MaterialStatusReason;
