import { cn } from "@/lib/utils";

type ReanalyzeAiToggleProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  title?: string;
  description?: string;
  className?: string;
  testId?: string;
};

export default function ReanalyzeAiToggle({
  checked,
  onChange,
  disabled = false,
  title = "启用 AI 重新解析",
  description = "取消勾选后仅执行本地规则分析，不调用 AI。",
  className,
  testId,
}: ReanalyzeAiToggleProps) {
  return (
    <label
      className={cn(
        "flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        className,
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        data-testid={testId}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-500"
        aria-label={title}
      />
      <div className="min-w-0">
        <div className="text-sm font-medium text-slate-800">{title}</div>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      </div>
    </label>
  );
}
