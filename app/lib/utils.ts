import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge 不读 tailwind.config.ts，默认按形状猜归属：不认识的 `text-xxx`
 * 算文字颜色，`shadow-xxx` 算阴影颜色，`bg-xxx` 算背景色，`rounded-xxx` 不归类。
 * 不登记的后果：`cn("text-kpi-value", "text-slate-900")` 会把字号类删掉，
 * `cn("rounded-md", "rounded-card")` 则两个都留下、谁生效看 CSS 顺序。
 *
 * tailwind.config.ts 的 fontSize / borderRadius / boxShadow / backgroundImage
 * 新增令牌时要同步登记在这里，tests/cnTailwindMerge.test.ts 会逐个令牌校验。
 * 自定义颜色不用登记：默认规则本来就把它们归到对应的颜色分组。
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["kpi-value", "caption"],
      radius: ["card"],
      shadow: ["soft", "float", "dialog"],
    },
    classGroups: {
      "bg-image": [{ bg: ["dialog-header-wash"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
