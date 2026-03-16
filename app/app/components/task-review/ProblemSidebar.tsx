import { Search, Filter, AlertCircle, CheckCircle2 } from "lucide-react";

import type { Problem } from "@/lib/mock";
import { cn } from "@/lib/utils";

interface ProblemSidebarProps {
  problems: Problem[];
  selectedId: string;
  onSelect: (id: string) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  categories: string[];
  activeCategory: string;
  onCategoryChange: (category: string) => void;
  highRiskOnly: boolean;
  onToggleHighRiskOnly: () => void;
}

export default function ProblemSidebar({
  problems,
  selectedId,
  onSelect,
  searchValue,
  onSearchChange,
  categories,
  activeCategory,
  onCategoryChange,
  highRiskOnly,
  onToggleHighRiskOnly,
}: ProblemSidebarProps) {
  return (
    <div className="flex h-full w-96 shrink-0 flex-col border-r border-border bg-white">
      <div className="shrink-0 space-y-4 border-b border-border p-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="搜索问题、规则编号..."
            className="w-full rounded-lg border border-border bg-slate-50 py-2 pl-9 pr-4 text-sm outline-none transition-all focus:border-primary-500 focus:ring-2 focus:ring-primary-500"
          />
        </div>

        <div className="flex items-center justify-between">
          <div className="scrollbar-hide flex gap-2 overflow-x-auto pb-1">
            {categories.map((category) => (
              <button
                key={category}
                type="button"
                onClick={() => onCategoryChange(category)}
                className={cn(
                  "whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium transition-colors",
                  activeCategory === category
                    ? "bg-slate-900 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                )}
              >
                {category}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onToggleHighRiskOnly}
            title={highRiskOnly ? "取消仅看高风险" : "仅看高风险"}
            className={cn(
              "ml-2 shrink-0 rounded-md p-1.5 transition-colors",
              highRiskOnly
                ? "bg-danger-50 text-danger-600 ring-1 ring-danger-200"
                : "bg-slate-50 text-slate-400 hover:text-primary-600"
            )}
          >
            <Filter className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto bg-slate-50/50 p-3">
        {problems.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-white px-4 py-8 text-center text-sm text-slate-500">
            当前筛选条件下没有问题
          </div>
        ) : (
          problems.map((problem) => (
            <div
              key={problem.id}
              onClick={() => onSelect(problem.id)}
              className={cn(
                "group relative cursor-pointer overflow-hidden rounded-xl border p-4 transition-all",
                selectedId === problem.id
                  ? "border-primary-500 bg-primary-50/30 shadow-sm ring-1 ring-primary-500"
                  : "border-border bg-white hover:border-slate-300 hover:shadow-sm"
              )}
            >
              <div
                className={cn(
                  "absolute inset-y-0 left-0 w-1",
                  problem.severity === "high"
                    ? "bg-danger-500"
                    : problem.severity === "warning"
                      ? "bg-warning-500"
                      : "bg-slate-300"
                )}
              />

              <div className="mb-2 flex items-start justify-between pl-2">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "rounded px-2 py-0.5 text-xs font-bold",
                      problem.category === "AI 智能分析"
                        ? "bg-blue-50 text-blue-600"
                        : "bg-purple-50 text-purple-600"
                    )}
                  >
                    {problem.category === "AI 智能分析" ? "AI 审查" : "本地规则"}
                  </span>
                  <span className="font-mono text-xs text-slate-500">{problem.ruleId}</span>
                </div>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-400">
                  第 {problem.page} 页
                </span>
              </div>

              <h3
                className={cn(
                  "mb-2 line-clamp-2 pl-2 text-sm font-semibold leading-snug",
                  selectedId === problem.id ? "text-primary-900" : "text-slate-900"
                )}
              >
                {problem.title}
              </h3>

              <div className="mt-3 flex items-center justify-between pl-2">
                <span className="text-xs text-slate-500">{problem.category}</span>
                {problem.status === "resolved" ? (
                  <span className="flex items-center gap-1 text-xs font-medium text-success-600">
                    <CheckCircle2 className="h-3 w-3" />
                    已复核
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-xs font-medium text-warning-600">
                    <AlertCircle className="h-3 w-3" />
                    待处理
                  </span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
