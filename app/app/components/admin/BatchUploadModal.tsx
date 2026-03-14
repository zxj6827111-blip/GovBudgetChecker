"use client";

import { useState, useRef, useEffect } from "react";
import { X, UploadCloud, FileText, CheckCircle2, AlertTriangle, ChevronRight, Loader2, Search, ChevronDown, Check } from "lucide-react";

import type { OrganizationRecord } from "@/lib/uiAdapters";
import { cn } from "@/lib/utils";

interface BatchUploadModalProps {
  onClose: () => void;
}

type MatchStatus = 'success' | 'warning';

interface UploadedFile {
  id: string;
  filename: string;
  year: string;
  departmentName: string;
  matchedOrgId: string | null;
  status: MatchStatus;
}

function SearchableOrgSelect({ value, onChange, options, hasError }: { value: string, onChange: (val: string) => void, options: any[], hasError: boolean }) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const filteredOptions = options.filter(opt => 
    opt.name.toLowerCase().includes(search.toLowerCase())
  );

  const selectedOption = options.find(opt => opt.id === value);

  return (
    <div ref={wrapperRef} className="relative w-full">
      <div 
        onClick={() => { setIsOpen(!isOpen); setSearch(''); }}
        className={cn(
          "w-full px-3 py-1.5 border rounded-md text-sm flex items-center justify-between cursor-pointer transition-colors",
          hasError ? "border-warning-300 bg-warning-50 text-warning-700" : "border-border bg-white text-slate-700 hover:border-primary-400"
        )}
      >
        <span className="truncate">{selectedOption ? selectedOption.name : "请选择所属单位"}</span>
        <ChevronDown className="w-4 h-4 text-slate-400 shrink-0 ml-2" />
      </div>

      {isOpen && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-border rounded-md shadow-xl max-h-60 flex flex-col overflow-hidden">
          <div className="p-2 border-b border-border flex items-center gap-2 shrink-0 bg-slate-50">
            <Search className="w-4 h-4 text-slate-400 shrink-0" />
            <input 
              type="text" 
              autoFocus
              className="w-full text-sm outline-none bg-transparent" 
              placeholder="搜索单位..." 
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
          <div className="overflow-y-auto p-1">
            {filteredOptions.length === 0 ? (
              <div className="p-3 text-sm text-slate-500 text-center">无匹配结果</div>
            ) : (
              filteredOptions.map(opt => (
                <div 
                  key={opt.id}
                  onClick={() => { onChange(opt.id); setIsOpen(false); }}
                  className={cn(
                    "px-2 py-2 text-sm rounded-md cursor-pointer flex items-center justify-between hover:bg-slate-100 transition-colors",
                    value === opt.id ? "text-primary-600 bg-primary-50 font-medium" : "text-slate-700"
                  )}
                >
                  <span className="truncate">
                    {opt.level === 1 ? <span className="text-slate-300 mr-1">├─</span> : ''}
                    {opt.name}
                  </span>
                  {value === opt.id && <Check className="w-4 h-4 shrink-0" />}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function BatchUploadModal({ onClose }: BatchUploadModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [isUploading, setIsUploading] = useState(false);
  const [orgOptions, setOrgOptions] = useState<{ id: string; name: string; level: number }[]>([]);
  
  // 模拟解析后的文件列表
  const [files, setFiles] = useState<UploadedFile[]>([]);

  // 扁平化组织架构用于下拉选择
  useEffect(() => {
    let alive = true;

    async function loadOrganizations() {
      try {
        const response = await fetch("/api/organizations/list", { cache: "no-store" });
        const payload = (await response.json()) as { organizations?: OrganizationRecord[] };
        if (!alive) {
          return;
        }

        const options = Array.isArray(payload.organizations)
          ? payload.organizations
              .map((org) => ({
                id: org.id,
                name: org.name,
                level: org.level === "unit" ? 1 : 0,
              }))
              .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"))
          : [];
        setOrgOptions(options);
      } catch {
        if (alive) {
          setOrgOptions([]);
        }
      }
    }

    void loadOrganizations();
    return () => {
      alive = false;
    };
  }, []);

  const handleSimulateUpload = () => {
    setIsUploading(true);
    // 模拟上传和解析延迟
    setTimeout(() => {
      setFiles([
        {
          id: 'f1',
          filename: '2025年市教育局部门预算草案.pdf',
          year: '2025',
          departmentName: '市教育局',
          matchedOrgId: 'org-2',
          status: 'success'
        },
        {
          id: 'f2',
          filename: '2024市财政局决算公开报告.pdf',
          year: '2024',
          departmentName: '市财政局',
          matchedOrgId: 'org-1',
          status: 'success'
        },
        {
          id: 'f3',
          filename: '2025年第三高级中学预算.pdf',
          year: '2025',
          departmentName: '第三高级中学',
          matchedOrgId: '', // 匹配失败，需要手动选择
          status: 'warning'
        },
        {
          id: 'f4',
          filename: '测试报告_最终版_v2.pdf',
          year: '', // 年份缺失
          departmentName: '', // 部门缺失
          matchedOrgId: '',
          status: 'warning'
        }
      ]);
      setIsUploading(false);
      setStep(2);
    }, 1500);
  };

  const handleOrgChange = (fileId: string, orgId: string) => {
    setFiles(prev => prev.map(f => {
      if (f.id === fileId) {
        const isFullyMatched = orgId !== '' && f.year !== '';
        return { ...f, matchedOrgId: orgId, status: isFullyMatched ? 'success' : 'warning' };
      }
      return f;
    }));
  };

  const handleYearChange = (fileId: string, year: string) => {
    setFiles(prev => prev.map(f => {
      if (f.id === fileId) {
        const isFullyMatched = f.matchedOrgId !== '' && year !== '';
        return { ...f, year, status: isFullyMatched ? 'success' : 'warning' };
      }
      return f;
    }));
  };

  const handleConfirm = () => {
    setStep(3);
    setTimeout(() => {
      onClose();
    }, 2000);
  };

  const allMatched = files.every(f => f.status === 'success');

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/60 flex items-center justify-center backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white rounded-2xl shadow-2xl w-[900px] max-h-[85vh] flex flex-col overflow-hidden animate-in zoom-in-95 duration-200">
        
        {/* Header */}
        <div className="px-6 py-4 border-b border-border flex justify-between items-center bg-slate-50/50 shrink-0">
          <div>
            <h2 className="text-lg font-bold text-slate-900">批量上传与智能匹配</h2>
            <div className="flex items-center gap-2 mt-1 text-sm font-medium">
              <span className={cn(step >= 1 ? "text-primary-600" : "text-slate-400")}>1. 上传文件</span>
              <ChevronRight className="w-4 h-4 text-slate-300" />
              <span className={cn(step >= 2 ? "text-primary-600" : "text-slate-400")}>2. 确认匹配关系</span>
              <ChevronRight className="w-4 h-4 text-slate-300" />
              <span className={cn(step >= 3 ? "text-primary-600" : "text-slate-400")}>3. 导入完成</span>
            </div>
          </div>
          <button onClick={onClose} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-md transition-colors">
            <X className="w-5 h-5"/>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 bg-surface-50">
          {step === 1 && (
            <div className="h-full flex flex-col items-center justify-center py-12">
              <div 
                onClick={handleSimulateUpload}
                className={cn(
                  "w-full max-w-2xl border-2 border-dashed rounded-2xl p-12 flex flex-col items-center justify-center text-center transition-all cursor-pointer group",
                  isUploading ? "border-primary-300 bg-primary-50/50" : "border-slate-300 hover:border-primary-500 hover:bg-primary-50/30 bg-white"
                )}
              >
                {isUploading ? (
                  <>
                    <Loader2 className="w-12 h-12 text-primary-600 animate-spin mb-4" />
                    <h3 className="text-lg font-semibold text-slate-900">正在解析文件...</h3>
                    <p className="text-sm text-slate-500 mt-2">系统正在提取文件名中的年度与单位信息</p>
                  </>
                ) : (
                  <>
                    <div className="w-16 h-16 bg-primary-50 text-primary-600 rounded-full flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                      <UploadCloud className="w-8 h-8" />
                    </div>
                    <h3 className="text-xl font-bold text-slate-900 mb-2">点击或拖拽文件到此处</h3>
                    <p className="text-slate-500 mb-6">支持批量选择多个 <span className="font-semibold text-slate-700">.pdf</span> 文件，或直接上传 <span className="font-semibold text-slate-700">.zip</span> 压缩包</p>
                    <div className="flex items-center gap-4 text-sm text-slate-400">
                      <span className="flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> 自动提取年度</span>
                      <span className="flex items-center gap-1"><CheckCircle2 className="w-4 h-4" /> 智能匹配组织架构</span>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4 animate-in slide-in-from-right-8 duration-300 pb-32">
              <div className="bg-warning-50 border border-warning-200 rounded-lg p-4 flex items-start gap-3">
                <AlertTriangle className="w-5 h-5 text-warning-600 shrink-0 mt-0.5" />
                <div>
                  <h4 className="text-sm font-semibold text-warning-900">请核对匹配结果</h4>
                  <p className="text-sm text-warning-800 mt-1">系统已尝试自动从文件名中提取信息。对于匹配失败或缺失的项，请手动选择正确的年度和所属单位。</p>
                </div>
              </div>

              <div className="bg-white border border-border rounded-xl shadow-sm">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b border-border text-sm font-semibold text-slate-600">
                      <th className="p-4 w-10 rounded-tl-xl">状态</th>
                      <th className="p-4">文件名</th>
                      <th className="p-4 w-32">年度</th>
                      <th className="p-4 w-64 rounded-tr-xl">所属单位</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {files.map((file, index) => (
                      <tr key={file.id} className={cn("hover:bg-slate-50/50 transition-colors", index === files.length - 1 ? "[&>td:first-child]:rounded-bl-xl [&>td:last-child]:rounded-br-xl" : "")}>
                        <td className="p-4 text-center">
                          {file.status === 'success' ? (
                            <CheckCircle2 className="w-5 h-5 text-success-600 mx-auto" />
                          ) : (
                            <AlertTriangle className="w-5 h-5 text-warning-500 mx-auto" />
                          )}
                        </td>
                        <td className="p-4">
                          <div className="flex items-center gap-2">
                            <FileText className="w-4 h-4 text-slate-400 shrink-0" />
                            <span className="font-medium text-slate-900 truncate max-w-[280px]" title={file.filename}>
                              {file.filename}
                            </span>
                          </div>
                        </td>
                        <td className="p-4">
                          <select 
                            value={file.year}
                            onChange={(e) => handleYearChange(file.id, e.target.value)}
                            className={cn(
                              "w-full px-2 py-1.5 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary-500",
                              !file.year ? "border-warning-300 bg-warning-50 text-warning-700" : "border-border bg-white text-slate-700"
                            )}
                          >
                            <option value="">请选择年度</option>
                            <option value="2025">2025</option>
                            <option value="2024">2024</option>
                            <option value="2023">2023</option>
                          </select>
                        </td>
                        <td className="p-4">
                          <SearchableOrgSelect 
                            value={file.matchedOrgId || ''}
                            onChange={(val) => handleOrgChange(file.id, val)}
                            options={orgOptions}
                            hasError={!file.matchedOrgId}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="h-full flex flex-col items-center justify-center py-16 animate-in zoom-in-95 duration-300">
              <div className="w-20 h-20 bg-success-50 rounded-full flex items-center justify-center mb-6">
                <CheckCircle2 className="w-10 h-10 text-success-600" />
              </div>
              <h3 className="text-2xl font-bold text-slate-900 mb-2">导入成功并已加入分析队列</h3>
              <p className="text-slate-500">共成功导入 {files.length} 份报告，系统正在后台进行智能审查。</p>
            </div>
          )}
        </div>

        {/* Footer */}
        {step === 2 && (
          <div className="px-6 py-4 border-t border-border bg-white flex justify-between items-center shrink-0">
            <span className="text-sm text-slate-500">
              已匹配: <span className="font-bold text-success-600">{files.filter(f => f.status === 'success').length}</span> / {files.length}
            </span>
            <div className="flex gap-3">
              <button 
                onClick={() => setStep(1)} 
                className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-border hover:bg-slate-50 rounded-lg transition-colors"
              >
                重新上传
              </button>
              <button 
                onClick={handleConfirm}
                disabled={!allMatched}
                className={cn(
                  "px-6 py-2 text-sm font-medium text-white rounded-lg transition-all shadow-sm flex items-center gap-2",
                  allMatched ? "bg-primary-600 hover:bg-primary-700" : "bg-slate-300 cursor-not-allowed"
                )}
              >
                确认导入并开始分析
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
