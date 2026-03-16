/* eslint-disable @next/next/no-img-element */

import { useState } from "react";
import {
  X,
  Download,
  Printer,
  FileText,
  CheckCircle,
  AlertTriangle,
  AlertCircle,
  Image as ImageIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

interface ReportPreviewModalProps {
  task: any;
  problems: any[];
  onClose: () => void;
}

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload?.detail || payload?.error || payload?.message || text || `HTTP ${response.status}`;
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function getFilenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) {
    return fallback;
  }

  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  return plainMatch?.[1] || fallback;
}

export default function ReportPreviewModal({ task, problems, onClose }: ReportPreviewModalProps) {
  const [isDownloading, setIsDownloading] = useState(false);
  const reportLabel = task.reportLabel ?? (task.type === "budget" ? "部门预算" : "部门决算");

  const handlePrint = () => {
    window.print();
  };

  const handleDownload = async () => {
    if (!task?.id || isDownloading) {
      return;
    }

    setIsDownloading(true);
    try {
      const response = await fetch(
        `/api/reports/download?job_id=${encodeURIComponent(task.id)}&format=pdf`,
        {
          cache: "no-store",
        }
      );

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const blob = await response.blob();
      const fallbackName = `${String(task.filename || task.id).replace(/\.pdf$/i, "")}_审查报告.pdf`;
      const filename = getFilenameFromDisposition(
        response.headers.get("content-disposition"),
        fallbackName
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "下载报告失败");
    } finally {
      setIsDownloading(false);
    }
  };

  const highCount = problems.filter((problem) => problem.severity === "high").length;
  const mediumCount = problems.filter((problem) => problem.severity === "warning").length;
  const lowCount = problems.filter((problem) => problem.severity === "info").length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm animate-in fade-in duration-200 sm:p-6">
      <div className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl animate-in zoom-in-95 duration-200">
        <div className="shrink-0 border-b border-border bg-slate-50 px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-primary-600" />
              <h3 className="text-lg font-bold text-slate-900">审查报告预览</h3>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handlePrint}
                className="flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
              >
                <Printer className="h-4 w-4" />
                打印
              </button>
              <button
                onClick={() => void handleDownload()}
                disabled={isDownloading}
                className="flex items-center gap-2 rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Download className="h-4 w-4" />
                {isDownloading ? "下载中..." : "下载 PDF"}
              </button>
              <div className="mx-1 h-6 w-px bg-slate-300" />
              <button
                onClick={onClose}
                className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto bg-slate-100 p-8 print:bg-white print:p-0">
          <div className="mx-auto max-w-3xl border border-slate-200 bg-white p-10 shadow-sm print:border-none print:p-0 print:shadow-none sm:p-16">
            <div className="mb-12 border-b-2 border-slate-900 pb-8 text-center">
              <h1 className="mb-4 text-3xl font-bold tracking-tight text-slate-900">
                {String(task.filename || "").replace(/\.pdf$/i, "")}
                <br />
                <span className="text-2xl">智能审查报告</span>
              </h1>
              <div className="mt-6 flex flex-wrap justify-center gap-x-8 gap-y-2 text-sm text-slate-600 [&>span:nth-child(4)]:hidden">
                <span className="flex items-center gap-1">
                  <span className="font-semibold text-slate-900">报告类型：</span>
                  {reportLabel}
                </span>
                <span className="flex items-center gap-1">
                  <span className="font-semibold text-slate-900">审查单位：</span>
                  {task.department}
                </span>
                <span className="flex items-center gap-1">
                  <span className="font-semibold text-slate-900">审查年度：</span>
                  {task.year}
                </span>
                <span className="flex items-center gap-1">
                  <span className="font-semibold text-slate-900">报告类型：</span>
                  {task.type === "budget" ? "部门预算" : "部门决算"}
                </span>
                <span className="flex items-center gap-1">
                  <span className="font-semibold text-slate-900">生成日期：</span>
                  {new Date().toLocaleDateString("zh-CN")}
                </span>
              </div>
            </div>

            <div className="mb-10">
              <h2 className="mb-4 flex items-center gap-2 text-lg font-bold text-slate-900">
                <span className="inline-block h-5 w-1.5 rounded-full bg-primary-600" />
                审查结论摘要
              </h2>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-5">
                <p className="mb-4 leading-relaxed text-slate-700">
                  本次审查共应用了 <span className="font-bold text-slate-900">128</span> 条合规性及逻辑性校验规则。
                  系统分析发现疑似问题 <span className="text-lg font-bold text-danger-600">{problems.length}</span> 个。
                  建议重点关注并核实以下高风险事项。
                </p>
                <div className="grid grid-cols-3 gap-4">
                  <div className="flex flex-col items-center justify-center rounded border border-slate-200 bg-white p-3">
                    <div className="mb-1 flex items-center gap-1.5 font-medium text-danger-600">
                      <AlertCircle className="h-4 w-4" />
                      高风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{highCount}</span>
                  </div>
                  <div className="flex flex-col items-center justify-center rounded border border-slate-200 bg-white p-3">
                    <div className="mb-1 flex items-center gap-1.5 font-medium text-warning-600">
                      <AlertTriangle className="h-4 w-4" />
                      中风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{mediumCount}</span>
                  </div>
                  <div className="flex flex-col items-center justify-center rounded border border-slate-200 bg-white p-3">
                    <div className="mb-1 flex items-center gap-1.5 font-medium text-slate-500">
                      <CheckCircle className="h-4 w-4" />
                      低风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{lowCount}</span>
                  </div>
                </div>
              </div>
            </div>

            <div>
              <h2 className="mb-4 flex items-center gap-2 text-lg font-bold text-slate-900">
                <span className="inline-block h-5 w-1.5 rounded-full bg-primary-600" />
                问题详情清单
              </h2>

              <div className="space-y-6">
                {problems.map((problem, index) => (
                  <div
                    key={problem.id}
                    className="break-inside-avoid overflow-hidden rounded-lg border border-slate-200"
                  >
                    <div className="flex items-start justify-between gap-4 border-b border-slate-200 bg-slate-50 px-4 py-3">
                      <div className="flex items-start gap-3">
                        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-200 text-sm font-bold text-slate-700">
                          {index + 1}
                        </span>
                        <div>
                          <h4 className="text-base font-bold leading-snug text-slate-900">
                            {problem.title}
                          </h4>
                          <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-500">
                            <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 font-mono">
                              {problem.ruleId}
                            </span>
                            <span>定位：{problem.location || `第 ${problem.page} 页`}</span>
                          </div>
                        </div>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 rounded border px-2 py-1 text-xs font-medium",
                          problem.severity === "high"
                            ? "border-danger-200 bg-danger-50 text-danger-700"
                            : problem.severity === "warning"
                              ? "border-warning-200 bg-warning-50 text-warning-700"
                              : "border-slate-200 bg-slate-100 text-slate-700"
                        )}
                      >
                        {problem.severity === "high"
                          ? "高风险"
                          : problem.severity === "warning"
                            ? "中风险"
                            : "低风险"}
                      </span>
                    </div>
                    <div className="bg-white p-4">
                      <div className="mb-4">
                        <span className="mb-1 block text-xs font-bold uppercase tracking-wider text-slate-500">
                          问题描述
                        </span>
                        <p className="text-sm leading-relaxed text-slate-800">{problem.description}</p>
                      </div>

                      {problem.evidenceImage ? (
                        <div className="mb-4 overflow-hidden rounded-md border border-slate-200">
                          <div className="flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-1.5">
                            <ImageIcon className="h-3.5 w-3.5 text-slate-400" />
                            <span className="text-xs font-medium text-slate-600">原文截图证据</span>
                          </div>
                          <div className="flex justify-center bg-slate-100/50 p-2">
                            <img
                              src={problem.evidenceImage}
                              alt="问题截图证据"
                              className="max-h-[200px] max-w-full rounded border border-slate-200 object-contain shadow-sm"
                              referrerPolicy="no-referrer"
                            />
                          </div>
                        </div>
                      ) : null}

                      {problem.suggestion ? (
                        <div className="rounded border border-primary-100 bg-primary-50/50 p-3">
                          <span className="mb-1 block text-xs font-bold uppercase tracking-wider text-primary-700">
                            整改建议
                          </span>
                          <p className="text-sm leading-relaxed text-primary-900">{problem.suggestion}</p>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
