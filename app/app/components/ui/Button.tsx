import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

import {
  BUTTON_BASE_CLASSES,
  BUTTON_SIZE_CLASSES,
  BUTTON_VARIANT_CLASSES,
  type ButtonSize,
  type ButtonVariant,
} from "./buttonStyles";

/**
 * Button 变体对照原型图取色（见 docs/UI_COLOR_TOKEN_MAPPING.md）：
 * - primary：原型图「上传 PDF」「开始分析」等主操作按钮，实心 primary-600 背景 + 白字。
 * - secondary：原型图「查看全部任务」「导出本页」等次要操作，白底 + slate 边框 + slate 文字。
 * - ghost：原型图顶栏图标按钮（搜索/通知/帮助），无边框无底色，仅 hover 时出现浅色背景。
 * - danger：破坏性操作（本轮 Task 1 尚无具体页面使用，为后续整改包删除等操作预留）。
 *
 * 无障碍：
 * - disabled 态用 aria-disabled 而非仅样式变灰，保证屏幕阅读器能感知；
 * - 所有变体在 focus-visible 时显示可见的焦点环（focus-visible:ring-2），
 *   不依赖鼠标 hover 也能定位当前焦点，满足键盘可达性要求。
 *
 * variant/size -> className 的映射表抽在 buttonStyles.ts（纯 .ts，无 JSX），
 * 便于用现有 jiti 测试脚本直接单测，原因见该文件顶部注释。
 */
export type { ButtonVariant, ButtonSize };

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children?: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  className,
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      aria-disabled={disabled || undefined}
      disabled={disabled}
      className={cn(BUTTON_BASE_CLASSES, BUTTON_VARIANT_CLASSES[variant], BUTTON_SIZE_CLASSES[size], className)}
      {...rest}
    >
      {children}
    </button>
  );
}

export default Button;
