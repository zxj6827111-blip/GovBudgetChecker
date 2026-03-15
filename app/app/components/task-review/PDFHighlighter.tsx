"use client";

import { ExternalLink, FileText, X } from "lucide-react";
import { useEffect } from "react";

import type { Problem } from "@/lib/mock";

import ProblemPreviewFrame from "./ProblemPreviewFrame";
import { buildProblemPdfPageUrl } from "./problemPreview";

interface PDFHighlighterProps {
  problem: Problem;
  onClose: () => void;
}

export default function PDFHighlighter({ problem, onClose }: PDFHighlighterProps) {
  const sourcePdfUrl = buildProblemPdfPageUrl(problem.jobId, problem.page);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-900/95 animate-in fade-in duration-200">
      <div className="border-b border-slate-800 bg-slate-950/85 px-6 py-4 text-slate-200 backdrop-blur-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-slate-200">
                <FileText className="h-3.5 w-3.5 text-slate-400" />
                第 {problem.page} 页
              </span>
              <span className="inline-flex items-center rounded-full border border-danger-500/30 bg-danger-500/10 px-3 py-1 text-danger-300">
                {problem.ruleId}
              </span>
            </div>

            <h2 className="text-lg font-semibold leading-snug text-white">{problem.title}</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {problem.location || problem.description}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            {sourcePdfUrl && (
              <a
                href={sourcePdfUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-slate-600 hover:bg-slate-700"
              >
                <ExternalLink className="h-4 w-4" />
                打开原页
              </a>
            )}

            <button
              onClick={onClose}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-danger-400 hover:bg-danger-500 hover:text-white"
            >
              关闭 (Esc)
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-6 lg:p-8">
        <div className="mx-auto max-w-[1200px]">
          <ProblemPreviewFrame
            problem={problem}
            frameClassName="rounded-2xl border border-slate-700 bg-white shadow-2xl"
            canvasClassName="min-h-[480px] bg-slate-100/80 p-6"
            imageClassName="max-h-[calc(100vh-15rem)]"
            overlayClassName="border-[1.5px] shadow-[0_0_0_9999px_rgba(15,23,42,0.18)]"
            labelClassName="border-danger-300 bg-white/94 text-danger-700 shadow-md"
          />
        </div>
      </div>
    </div>
  );
}
