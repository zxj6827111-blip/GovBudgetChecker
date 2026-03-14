/* eslint-disable @next/next/no-img-element */

import { X, Download, Printer, FileText, CheckCircle, AlertTriangle, AlertCircle, Image as ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface ReportPreviewModalProps {
  task: any;
  problems: any[];
  onClose: () => void;
}

export default function ReportPreviewModal({ task, problems, onClose }: ReportPreviewModalProps) {
  const reportLabel = task.reportLabel ?? (task.type === "budget" ? "部门预算" : "部门决算");

  const handlePrint = () => {
    window.print();
  };

  const handleDownload = () => {
    // 模拟下载过程
    const content = `
${task.filename} - 审查报告
=========================================
审查单位：${task.department}
审查年度：${task.year}
报告类型：${task.type === 'budget' ? '部门预算' : '部门决算'}
审查状态：${task.status === 'completed' ? '分析完成' : '分析异常'}
发现问题数：${problems.length} 个

问题详情：
${problems.map((p, i) => `
${i + 1}. [${p.ruleId}] ${p.title}
   严重程度：${p.severity === 'high' ? '高' : p.severity === 'warning' ? '中' : '低'}
   定位：${p.location || `第${p.page}页`}
   描述：${p.description}
`).join('\n')}
    `;
    
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${task.filename.replace('.pdf', '')}_审查报告.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const highCount = problems.filter(p => p.severity === 'high').length;
  const mediumCount = problems.filter(p => p.severity === 'warning').length;
  const lowCount = problems.filter(p => p.severity === 'info').length;

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200 p-4 sm:p-6">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-full flex flex-col overflow-hidden animate-in zoom-in-95 duration-200">
        
        {/* Header */}
        <div className="px-6 py-4 border-b border-border flex justify-between items-center bg-slate-50 shrink-0">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-primary-600" />
            <h3 className="font-bold text-slate-900 text-lg">审查报告预览</h3>
          </div>
          <div className="flex items-center gap-2">
            <button 
              onClick={handlePrint}
              className="px-3 py-1.5 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors flex items-center gap-2"
            >
              <Printer className="w-4 h-4" />
              打印
            </button>
            <button 
              onClick={handleDownload}
              className="px-3 py-1.5 text-sm font-medium text-white bg-primary-600 rounded-md hover:bg-primary-700 transition-colors flex items-center gap-2 shadow-sm"
            >
              <Download className="w-4 h-4" />
              下载 Word/PDF
            </button>
            <div className="w-px h-6 bg-slate-300 mx-1"></div>
            <button onClick={onClose} className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-200 rounded-md transition-colors">
              <X className="w-5 h-5"/>
            </button>
          </div>
        </div>

        {/* Report Content (Printable Area) */}
        <div className="flex-1 overflow-y-auto p-8 bg-slate-100 print:bg-white print:p-0">
          <div className="max-w-3xl mx-auto bg-white p-10 sm:p-16 shadow-sm border border-slate-200 print:shadow-none print:border-none print:p-0">
            
            {/* Report Header */}
            <div className="text-center mb-12 border-b-2 border-slate-900 pb-8">
              <h1 className="text-3xl font-bold text-slate-900 mb-4 tracking-tight">
                {task.filename.replace('.pdf', '')}
                <br />
                <span className="text-2xl">智能审查报告</span>
              </h1>
              <div className="flex flex-wrap justify-center gap-x-8 gap-y-2 text-sm text-slate-600 mt-6 [&>span:nth-child(4)]:hidden">
                <span className="flex items-center gap-1"><span className="font-semibold text-slate-900">报告类型：</span>{reportLabel}</span>
                <span className="flex items-center gap-1"><span className="font-semibold text-slate-900">审查单位：</span>{task.department}</span>
                <span className="flex items-center gap-1"><span className="font-semibold text-slate-900">审查年度：</span>{task.year}</span>
                <span className="flex items-center gap-1"><span className="font-semibold text-slate-900">报告类型：</span>{task.type === 'budget' ? '部门预算' : '部门决算'}</span>
                <span className="flex items-center gap-1"><span className="font-semibold text-slate-900">生成日期：</span>{new Date().toLocaleDateString('zh-CN')}</span>
              </div>
            </div>

            {/* Summary Section */}
            <div className="mb-10">
              <h2 className="text-lg font-bold text-slate-900 mb-4 flex items-center gap-2">
                <span className="w-1.5 h-5 bg-primary-600 rounded-full inline-block"></span>
                审查结论摘要
              </h2>
              <div className="bg-slate-50 p-5 rounded-lg border border-slate-200">
                <p className="text-slate-700 leading-relaxed mb-4">
                  本次审查共应用 <span className="font-bold text-slate-900">128</span> 条合规性及逻辑性校验规则。
                  系统分析发现疑似问题 <span className="font-bold text-danger-600 text-lg">{problems.length}</span> 个。
                  建议重点关注并核实以下高风险事项。
                </p>
                <div className="grid grid-cols-3 gap-4">
                  <div className="bg-white p-3 rounded border border-slate-200 flex flex-col items-center justify-center">
                    <div className="flex items-center gap-1.5 text-danger-600 font-medium mb-1">
                      <AlertCircle className="w-4 h-4" /> 高风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{highCount}</span>
                  </div>
                  <div className="bg-white p-3 rounded border border-slate-200 flex flex-col items-center justify-center">
                    <div className="flex items-center gap-1.5 text-warning-600 font-medium mb-1">
                      <AlertTriangle className="w-4 h-4" /> 中风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{mediumCount}</span>
                  </div>
                  <div className="bg-white p-3 rounded border border-slate-200 flex flex-col items-center justify-center">
                    <div className="flex items-center gap-1.5 text-slate-500 font-medium mb-1">
                      <CheckCircle className="w-4 h-4" /> 低风险
                    </div>
                    <span className="text-2xl font-bold text-slate-900">{lowCount}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Detailed Problems */}
            <div>
              <h2 className="text-lg font-bold text-slate-900 mb-4 flex items-center gap-2">
                <span className="w-1.5 h-5 bg-primary-600 rounded-full inline-block"></span>
                问题详情清单
              </h2>
              
              <div className="space-y-6">
                {problems.map((problem, index) => (
                  <div key={problem.id} className="border border-slate-200 rounded-lg overflow-hidden break-inside-avoid">
                    <div className="bg-slate-50 px-4 py-3 border-b border-slate-200 flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3">
                        <span className="flex items-center justify-center w-6 h-6 rounded-full bg-slate-200 text-slate-700 text-sm font-bold shrink-0 mt-0.5">
                          {index + 1}
                        </span>
                        <div>
                          <h4 className="font-bold text-slate-900 text-base leading-snug">{problem.title}</h4>
                          <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-500">
                            <span className="font-mono bg-white px-1.5 py-0.5 rounded border border-slate-200">{problem.ruleId}</span>
                            <span>定位：{problem.location || `第${problem.page}页`}</span>
                          </div>
                        </div>
                      </div>
                      <span className={cn(
                        "px-2 py-1 rounded text-xs font-medium shrink-0 border",
                        problem.severity === 'high' ? "bg-danger-50 text-danger-700 border-danger-200" :
                        problem.severity === 'warning' ? "bg-warning-50 text-warning-700 border-warning-200" :
                        "bg-slate-100 text-slate-700 border-slate-200"
                      )}>
                        {problem.severity === 'high' ? '高风险' : problem.severity === 'warning' ? '中风险' : '低风险'}
                      </span>
                    </div>
                    <div className="p-4 bg-white">
                      <div className="mb-4">
                        <span className="text-xs font-bold text-slate-500 uppercase tracking-wider block mb-1">问题描述</span>
                        <p className="text-sm text-slate-800 leading-relaxed">{problem.description}</p>
                      </div>
                      
                      {/* Evidence Image Section */}
                      {problem.evidenceImage && (
                        <div className="mb-4 border border-slate-200 rounded-md overflow-hidden">
                          <div className="bg-slate-50 px-3 py-1.5 border-b border-slate-200 flex items-center gap-2">
                            <ImageIcon className="w-3.5 h-3.5 text-slate-400" />
                            <span className="text-xs font-medium text-slate-600">原文截图证据</span>
                          </div>
                          <div className="p-2 bg-slate-100/50 flex justify-center">
                            <img 
                              src={problem.evidenceImage} 
                              alt="问题截图证据" 
                              className="max-w-full h-auto max-h-[200px] object-contain rounded border border-slate-200 shadow-sm"
                              referrerPolicy="no-referrer"
                            />
                          </div>
                        </div>
                      )}

                      {problem.suggestion && (
                        <div className="bg-primary-50/50 p-3 rounded border border-primary-100">
                          <span className="text-xs font-bold text-primary-700 uppercase tracking-wider block mb-1">整改建议</span>
                          <p className="text-sm text-primary-900 leading-relaxed">{problem.suggestion}</p>
                        </div>
                      )}
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
