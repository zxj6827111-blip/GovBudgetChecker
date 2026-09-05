/** Badge 的纯逻辑层：tone -> className 映射。拆分原因见 buttonStyles.ts 顶部注释。 */

export type BadgeTone = "review" | "processing" | "lowconf" | "failed" | "done" | "neutral";

export const BADGE_TONE_CLASSES: Record<BadgeTone, string> = {
  review: "bg-warning-100 text-warning-700",
  processing: "bg-primary-100 text-primary-700",
  lowconf: "bg-warning-100 text-warning-700",
  failed: "bg-danger-100 text-danger-700",
  done: "bg-success-100 text-success-700",
  neutral: "bg-slate-100 text-slate-600",
};

export const BADGE_BASE_CLASSES = "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium";
