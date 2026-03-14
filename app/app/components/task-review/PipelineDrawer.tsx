import { ChevronLeft, ChevronRight, CheckCircle2, CircleDashed, Loader2, Database, FileText } from "lucide-react";
import { Task } from "@/lib/mock";
import { cn } from "@/lib/utils";

interface PipelineDrawerProps {
  isOpen: boolean;
  onToggle: () => void;
  task: Task;
}

export default function PipelineDrawer({ isOpen, onToggle, task }: PipelineDrawerProps) {
  const steps = [
    { id: 'parse', label: '文档解析', status: task.pipeline.parse },
    { id: 'extract', label: '数据识别', status: task.pipeline.extract },
    { id: 'review', label: '智能审查', status: task.pipeline.review },
    { id: 'report', label: '报告生成', status: task.pipeline.report },
  ];

  return (
    <div className={cn(
      "flex flex-col border-r border-border bg-white transition-all duration-300 relative shrink-0",
      isOpen ? "w-64" : "w-12"
    )}>
      <button 
        onClick={onToggle}
        className="absolute -right-3 top-6 w-6 h-6 bg-white border border-border rounded-full flex items-center justify-center shadow-sm z-10 hover:bg-slate-50 text-slate-500 hover:text-primary-600 transition-colors"
      >
        {isOpen ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </button>

      <div className={cn("p-4 border-b border-border flex items-center", !isOpen && "justify-center")}>
        <FileText className="w-5 h-5 text-slate-400 shrink-0" />
        {isOpen && <span className="ml-2 font-semibold text-sm text-slate-700">分析流水线</span>}
      </div>

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="p-4">
          {steps.map((step, idx) => (
            <div key={step.id} className="flex items-start mb-6 relative group">
              {idx !== steps.length - 1 && (
                <div className={cn(
                  "absolute left-2.5 top-6 w-px h-full -ml-px",
                  step.status === 'done' ? "bg-success-500" : "bg-slate-200"
                )} />
              )}
              <div className="relative z-10 flex items-center justify-center w-5 h-5 bg-white shrink-0">
                {step.status === 'done' ? (
                  <CheckCircle2 className="w-5 h-5 text-success-600" />
                ) : step.status === 'processing' ? (
                  <Loader2 className="w-5 h-5 text-primary-600 animate-spin" />
                ) : (
                  <CircleDashed className="w-5 h-5 text-slate-300" />
                )}
              </div>
              {isOpen && (
                <div className="ml-3">
                  <p className={cn(
                    "text-sm font-medium",
                    step.status === 'done' ? "text-slate-900" : step.status === 'processing' ? "text-primary-700" : "text-slate-400"
                  )}>
                    {step.label}
                  </p>
                  {step.status === 'processing' && <p className="text-xs text-slate-500 mt-0.5">正在处理中...</p>}
                </div>
              )}
            </div>
          ))}
        </div>

        {isOpen && (
          <div className="mt-4 border-t border-border p-4">
            <div className="flex items-center gap-2 mb-4">
              <Database className="w-4 h-4 text-slate-400" />
              <h3 className="text-sm font-semibold text-slate-700">数据资产入库状态</h3>
            </div>
            <div className="space-y-3">
              <div className="flex justify-between items-center text-sm">
                <span className="text-slate-500">识别表数</span>
                <span className="font-medium text-slate-900">{task.structuredData.tables}</span>
              </div>
              <div className="flex justify-between items-center text-sm">
                <span className="text-slate-500">Facts数</span>
                <span className="font-medium text-slate-900">{task.structuredData.facts}</span>
              </div>
              <div className="flex justify-between items-center text-sm">
                <span className="text-slate-500">共享库落库</span>
                <span className={cn(
                  "px-2 py-0.5 rounded text-xs font-medium",
                  task.structuredData.syncStatus === 'synced' ? "bg-success-50 text-success-700" : "bg-warning-50 text-warning-700"
                )}>
                  {task.structuredData.syncStatus === 'synced' ? '已同步' : '待同步'}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
