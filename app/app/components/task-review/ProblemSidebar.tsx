import { Search, Filter, AlertCircle, CheckCircle2 } from "lucide-react";
import { Problem } from "@/lib/mock";
import { cn } from "@/lib/utils";

interface ProblemSidebarProps {
  problems: Problem[];
  selectedId: string;
  onSelect: (id: string) => void;
}

export default function ProblemSidebar({ problems, selectedId, onSelect }: ProblemSidebarProps) {
  const categories = ["全部", "基础信息合规", "表格完整性", "表内逻辑关系", "文数一致性", "AI 智能分析"];

  return (
    <div className="w-96 border-r border-border bg-white flex flex-col shrink-0 h-full">
      <div className="p-4 border-b border-border space-y-4 shrink-0">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input 
            type="text" 
            placeholder="搜索问题、规则编号..." 
            className="w-full bg-slate-50 border border-border rounded-lg pl-9 pr-4 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none transition-all"
          />
        </div>
        
        <div className="flex items-center justify-between">
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
            {categories.map((cat, i) => (
              <button 
                key={cat}
                className={cn(
                  "px-3 py-1 rounded-full text-xs font-medium whitespace-nowrap transition-colors",
                  i === 0 ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                )}
              >
                {cat}
              </button>
            ))}
          </div>
          <button className="p-1.5 text-slate-400 hover:text-primary-600 bg-slate-50 rounded-md shrink-0 ml-2">
            <Filter className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2 bg-slate-50/50">
        {problems.map(prob => (
          <div 
            key={prob.id}
            onClick={() => onSelect(prob.id)}
            className={cn(
              "p-4 rounded-xl border cursor-pointer transition-all relative overflow-hidden group",
              selectedId === prob.id 
                ? "bg-primary-50/30 border-primary-500 shadow-sm ring-1 ring-primary-500" 
                : "bg-white border-border hover:border-slate-300 hover:shadow-sm"
            )}
          >
            {/* 严重度左侧指示条 */}
            <div className={cn(
              "absolute left-0 top-0 bottom-0 w-1",
              prob.severity === 'high' ? "bg-danger-500" : 
              prob.severity === 'warning' ? "bg-warning-500" : "bg-slate-300"
            )} />

            <div className="flex justify-between items-start mb-2 pl-2">
              <div className="flex items-center gap-2">
                {/* 规则来源标识：AI 还是 本地规则 */}
                <span className={cn(
                  "px-2 py-0.5 rounded text-xs font-bold",
                  prob.category === 'AI 智能分析' ? "bg-blue-50 text-blue-600" : "bg-purple-50 text-purple-600"
                )}>
                  {prob.category === 'AI 智能分析' ? 'AI 审查' : '本地规则'}
                </span>
                <span className="text-xs font-mono text-slate-500">{prob.ruleId}</span>
              </div>
              <span className="text-xs font-medium text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">第 {prob.page} 页</span>
            </div>
            
            <h3 className={cn(
              "text-sm font-semibold leading-snug pl-2 mb-2 line-clamp-2",
              selectedId === prob.id ? "text-primary-900" : "text-slate-900"
            )}>
              {prob.title}
            </h3>

            <div className="flex items-center justify-between pl-2 mt-3">
              <span className="text-xs text-slate-500">{prob.category}</span>
              {prob.status === 'resolved' ? (
                <span className="text-xs font-medium text-success-600 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> 已复核
                </span>
              ) : (
                <span className="text-xs font-medium text-warning-600 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> 待处理
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
