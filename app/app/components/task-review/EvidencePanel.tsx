import type { Problem } from "@/lib/mock";
import { cn } from "@/lib/utils";

import ProblemPreviewFrame from "./ProblemPreviewFrame";
import { buildProblemPdfPageUrl } from "./problemPreview";

interface EvidencePanelProps {
  problem: Problem;
  onOpenViewer: () => void;
}

export default function EvidencePanel({ problem, onOpenViewer }: EvidencePanelProps) {
  const sourcePdfUrl = buildProblemPdfPageUrl(problem.jobId, problem.page);

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden rounded-xl border border-border bg-white shadow-sm">
      <div className="flex-1 space-y-6 overflow-y-auto p-8">
        <div className="mb-2 flex items-start justify-between">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium",
                problem.category === "AI 智能分析"
                  ? "border-blue-100 bg-blue-50 text-blue-600"
                  : "border-purple-100 bg-purple-50 text-purple-600",
              )}
            >
              {problem.category === "AI 智能分析" ? "AI 审查" : "本地规则"}
            </span>
            <span
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium",
                problem.severity === "high"
                  ? "border-danger-100 bg-danger-50 text-danger-700"
                  : problem.severity === "warning"
                    ? "border-warning-100 bg-warning-50 text-warning-700"
                    : "border-slate-200 bg-slate-100 text-slate-700",
              )}
            >
              {problem.severity === "high"
                ? "高风险"
                : problem.severity === "warning"
                  ? "需关注"
                  : "提示"}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              {problem.ruleId}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              第 {problem.page} 页
            </span>
          </div>

          <button className="rounded-lg border border-danger-100 bg-danger-50 px-4 py-1.5 text-sm font-medium text-danger-600 transition-colors hover:bg-danger-100">
            忽略此问题
          </button>
        </div>

        <div>
          <h2 className="mb-2 text-xl font-bold leading-snug text-slate-900">{problem.title}</h2>
          <p className="text-sm text-slate-500">第 {problem.page} 页</p>
        </div>

        <div className="rounded-xl border border-slate-100 bg-slate-50 p-6">
          <h4 className="mb-4 text-base font-medium text-slate-700">问题详情</h4>
          <ul className="space-y-3 text-sm text-slate-600">
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">{problem.description}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">定位：{problem.location || `第 ${problem.page} 页`}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
              <span className="leading-relaxed">证据：{problem.snippet}</span>
            </li>
          </ul>
        </div>

        <div className="rounded-xl border border-danger-100 bg-danger-50/30 p-6">
          <div className="mb-1 flex items-center justify-between">
            <h4 className="text-base font-medium text-danger-900">证据预览</h4>
            <div className="flex items-center gap-4 text-sm">
              <button
                onClick={onOpenViewer}
                className="font-medium text-primary-600 transition-colors hover:text-primary-700"
              >
                查看大图
              </button>
              {sourcePdfUrl && (
                <a
                  href={sourcePdfUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary-600 transition-colors hover:text-primary-700"
                >
                  打开原页
                </a>
              )}
            </div>
          </div>

          <p className="mb-4 text-sm text-danger-600">
            {problem.bbox
              ? "当前截图已改为原始 PDF 页面截图，并在原表格位置叠加红框和问题描述。"
              : "当前问题暂无定位框，显示原始 PDF 页面截图。"}
          </p>

          <ProblemPreviewFrame
            problem={problem}
            onClick={onOpenViewer}
            frameClassName="rounded-xl border border-danger-200 bg-white shadow-sm"
            canvasClassName="min-h-[260px]"
            imageClassName="max-h-[520px]"
            overlayClassName="shadow-[0_0_0_9999px_rgba(15,23,42,0.06)]"
            showHoverHint
            hoverHintText="点击查看大图"
          />
        </div>

        <div className="rounded-xl border border-slate-100 bg-slate-50 p-6">
          <h4 className="mb-4 text-base font-medium text-slate-700">原文证据</h4>
          <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm leading-relaxed text-slate-700 shadow-sm">
            {problem.snippet}
          </div>
        </div>
      </div>
    </div>
  );
}
