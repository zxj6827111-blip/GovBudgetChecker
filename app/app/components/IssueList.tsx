"use client";

import { useState, useMemo } from "react";
import { IssueItem } from "./IssueTabs";

interface IssueListProps {
  issues: IssueItem[];
  onIssueClick?: (issue: IssueItem) => void;
  showSource?: boolean;
  title?: string;
}

type CategoryDef = {
  id: string;
  name: string;
  description: string;
  prefixes: string[];
  exactMatches?: string[];
};

const CATEGORIES: CategoryDef[] = [
  {
    id: "basic",
    name: "基础信息合规性",
    description: "封面及目录的年份、单位识别与一致性检查",
    prefixes: ["V33-001"],
  },
  {
    id: "integrity",
    name: "核心表格完整性",
    description: "检查“九张表”是否齐全、定位是否准确、顺序是否符合目录要求",
    prefixes: ["V33-002"],
  },
  {
    id: "quality",
    name: "文档质量与规范",
    description: "页数、文件大小、空表说明等规范性检查",
    prefixes: ["V33-003"],
    exactMatches: ["V33-114"],
  },
  {
    id: "logic",
    name: "表内数据逻辑",
    description: "单张表格内部的数值合法性及计算逻辑校验",
    prefixes: ["V33-004", "V33-005"],
  },
  {
    id: "consistency",
    name: "跨表数据一致性",
    description: "检查不同表格之间相同指标的数据是否一致",
    prefixes: ["V33-1"], // V33-1xx
  },
  {
    id: "text-data",
    name: "文数一致性",
    description: "检查文字描述中的数据与表格中的数据是否一致",
    prefixes: ["V33-2"],
  },
];

export default function IssueList({
  issues,
  onIssueClick,
  showSource = false,
  title
}: IssueListProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [expandedCats, setExpandedCats] = useState<Record<string, boolean>>({});

  const filteredIssues = useMemo(() => {
    return issues.filter((issue) => {
      if (!searchTerm) return true;
      const lower = searchTerm.toLowerCase();
      return (
        issue.title.toLowerCase().includes(lower) ||
        issue.message.toLowerCase().includes(lower) ||
        issue.tags.some(tag => tag.toLowerCase().includes(lower))
      );
    });
  }, [issues, searchTerm]);

  const grouped = useMemo(() => {
    const groups: Record<string, IssueItem[]> = {
      ai: [],
      other: [],
    };
    CATEGORIES.forEach(c => groups[c.id] = []);

    filteredIssues.forEach(issue => {
      if (issue.source === "ai") {
        groups["ai"].push(issue);
        return;
      }

      const rid = issue.rule_id || "";
      let matched = false;

      for (const cat of CATEGORIES) {
        if (cat.exactMatches?.includes(rid)) {
          groups[cat.id].push(issue);
          matched = true;
          break;
        }
      }

      if (!matched) {
        for (const cat of CATEGORIES) {
          if (cat.prefixes.some(p => rid.startsWith(p))) {
            groups[cat.id].push(issue);
            matched = true;
            break;
          }
        }
      }

      if (!matched) {
        groups["other"].push(issue);
      }
    });

    return groups;
  }, [filteredIssues]);

  const toggleCat = (id: string) => {
    setExpandedCats(prev => ({ ...prev, [id]: !prev[id] }));
  };

  const getSeverityBadge = (severity: string) => {
    const defaultClasses = "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold";
    const colors = {
      critical: "bg-red-100 text-red-700 border border-red-200",
      high: "bg-red-100 text-red-700 border border-red-200",
      medium: "bg-yellow-100 text-yellow-700 border border-yellow-200",
      low: "bg-blue-100 text-blue-700 border border-blue-200",
      info: "bg-gray-100 text-gray-700 border border-gray-200",
    };
    return `${defaultClasses} ${colors[severity as keyof typeof colors] || colors.info}`;
  };

  const getSeverityText = (severity: string) => {
    const texts = {
      critical: "严重",
      high: "高",
      medium: "警告",
      low: "低",
      info: "信息",
    };
    return texts[severity as keyof typeof texts] || severity;
  };

  const getSeverityDot = (severity: string) => {
    switch (severity) {
      case "critical":
      case "high": return <span className="w-1.5 h-1.5 rounded-full bg-red-500 mr-1.5"></span>;
      case "medium": return <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 mr-1.5"></span>;
      case "low":
      case "info": return <span className="w-1.5 h-1.5 rounded-full bg-blue-500 mr-1.5"></span>;
      default: return null;
    }
  };

  const toPositivePage = (value: unknown): number | null => {
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return Math.floor(value);
    }
    if (typeof value === "string") {
      const parsed = Number(value);
      if (Number.isFinite(parsed) && parsed > 0) {
        return Math.floor(parsed);
      }
    }
    return null;
  };

  const getIssuePageInfo = (issue: IssueItem): { page: number | null; source: "location" | "evidence" | "page_number" | null } => {
    const fromLocation = toPositivePage(issue.location?.page);
    if (fromLocation) return { page: fromLocation, source: "location" };

    const firstEvidence = Array.isArray(issue.evidence) ? issue.evidence[0] : null;
    const fromEvidence = toPositivePage(firstEvidence?.page);
    if (fromEvidence) return { page: fromEvidence, source: "evidence" };

    const fromPageNumber = toPositivePage(issue.page_number);
    if (fromPageNumber) return { page: fromPageNumber, source: "page_number" };

    return { page: null, source: null };
  };

  const getIssueLocationHint = (issue: IssueItem): string => {
    const locationParts: string[] = [];
    if (issue.location?.table) locationParts.push(`表: ${issue.location.table}`);
    if (issue.location?.section) locationParts.push(`章节: ${issue.location.section}`);
    if (issue.location?.row) locationParts.push(`行: ${issue.location.row}`);
    if (issue.location?.col) locationParts.push(`列: ${issue.location.col}`);
    if (locationParts.length > 0) return locationParts.join("，");

    const firstEvidence = Array.isArray(issue.evidence) ? issue.evidence[0] : null;
    const snippet = String(firstEvidence?.text || firstEvidence?.text_snippet || "").replace(/\s+/g, " ").trim();
    if (!snippet) {
      return "未提取到页码或结构化坐标，请根据问题描述人工复核。";
    }

    const keyword = snippet.length > 24 ? `${snippet.slice(0, 24)}...` : snippet;
    return `可在PDF中搜索关键词“${keyword}”`;
  };

  const getEvidenceSnippet = (issue: IssueItem): string => {
    const firstEvidence = Array.isArray(issue.evidence) ? issue.evidence[0] : null;
    const raw = String(firstEvidence?.text || firstEvidence?.text_snippet || "").replace(/\s+/g, " ").trim();
    if (!raw) return "";
    if (raw === issue.title || raw === issue.message) return "";
    return raw.length > 90 ? `${raw.slice(0, 90)}...` : raw;
  };


  const renderCategoryTable = (id: string, name: string, description: string, items: IssueItem[], alwaysShow = true) => {
    if (!alwaysShow && items.length === 0) return null;

    const hasIssues = items.length > 0;
    const isExpanded = expandedCats[id] ?? hasIssues; 

    return (
      <div key={id} className="border border-gray-200 rounded-xl overflow-hidden mb-6 bg-white shadow-sm">
        <div
          className={`px-5 py-4 flex items-center justify-between cursor-pointer transition-colors ${hasIssues ? "bg-white hover:bg-gray-50 border-b border-gray-100" : "bg-gray-50 hover:bg-gray-100"
            }`}
          onClick={() => toggleCat(id)}
        >
          <div className="flex items-center space-x-3">
            <h4 className="font-bold text-gray-900 text-lg">{name}</h4>
            <span className="text-sm text-gray-500 bg-gray-100 px-2 py-0.5 rounded-md hidden sm:block">{description}</span>
          </div>
          <div className="flex items-center space-x-4">
            {hasIssues ? (
              <span className="bg-red-50 text-red-600 px-2.5 py-1 rounded-md text-sm font-semibold border border-red-100 flex items-center shadow-sm">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500 mr-2"></span>
                {items.length} 个问题
              </span>
            ) : (
               <span className="bg-green-50 text-green-600 px-2.5 py-1 rounded-md text-sm font-semibold border border-green-100 flex items-center shadow-sm">
                <svg className="w-4 h-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                全部通过
              </span>
            )}
            <div className="p-1 rounded-full hover:bg-gray-200 transition-colors">
              <svg
                className={`w-5 h-5 text-gray-400 transition-transform duration-200 ${isExpanded ? "transform rotate-180" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
        </div>

        {/* Table Content */}
        {isExpanded && hasIssues && (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200 text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  <th className="py-3 px-5 w-16 text-center">序号</th>
                  {showSource && <th className="py-3 px-5 w-24">来源</th>}
                  <th className="py-3 px-5 w-32">严重程度</th>
                  <th className="py-3 px-5 w-40">规则编号</th>
                  <th className="py-3 px-5 min-w-[300px]">问题描述</th>
                  <th className="py-3 px-5 w-32">证据页码</th>
                  <th className="py-3 px-5 w-32 text-center">状态</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((issue, index) => {
                  const pageInfo = getIssuePageInfo(issue);
                  const locationHint = getIssueLocationHint(issue);
                  const evidenceSnippet = getEvidenceSnippet(issue);
                  return (
                  <tr
                    key={issue.id}
                    className="hover:bg-indigo-50/30 transition-colors group cursor-pointer"
                    onClick={() => onIssueClick?.(issue)}
                  >
                    <td className="py-4 px-5 text-center text-sm text-gray-500 font-medium">{(index + 1).toString().padStart(2, '0')}</td>
                    {showSource && (
                      <td className="py-4 px-5">
                         <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${issue.source === 'ai' ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-purple-50 text-purple-700 border-purple-200'}`}>
                           {issue.source === 'ai' ? 'AI' : '规则'}
                         </span>
                      </td>
                    )}
                    <td className="py-4 px-5">
                      <span className={getSeverityBadge(issue.severity)}>
                        {getSeverityDot(issue.severity)}
                        {getSeverityText(issue.severity)}
                      </span>
                    </td>
                    <td className="py-4 px-5">
                      {issue.rule_id ? (
                        <span className="font-mono text-sm text-gray-600 bg-gray-50 px-2 py-1 rounded border border-gray-200">{issue.rule_id}</span>
                      ) : (
                        <span className="text-gray-400 text-sm">-</span>
                      )}
                    </td>
                    <td className="py-4 px-5">
                      <p className="text-sm font-medium text-gray-900 mb-1 leading-snug group-hover:text-indigo-600 transition-colors">{issue.title}</p>
                      {issue.message !== issue.title && (
                         <p className="text-xs text-gray-500 line-clamp-1">{issue.message}</p>
                      )}
                      {evidenceSnippet && (
                        <p className="text-xs text-slate-600 mt-1 line-clamp-2">命中原文：{evidenceSnippet}</p>
                      )}
                      {!pageInfo.page && (
                        <p className="text-xs text-amber-600 mt-1">定位提示：{locationHint}</p>
                      )}
                    </td>
                    <td className="py-4 px-5">
                       {pageInfo.page ? (
                         <div className="flex items-center text-sm text-indigo-600 hover:text-indigo-800 font-medium">
                            <svg className="w-4 h-4 mr-1 opacity-70" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                            P{pageInfo.page}
                            {pageInfo.source !== "location" && (
                              <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-indigo-50 text-indigo-500 border border-indigo-100">
                                {pageInfo.source === "evidence" ? "证据推断" : "字段推断"}
                              </span>
                            )}
                         </div>
                       ) : (
                         <div className="text-xs text-amber-600 leading-5">{locationHint}</div>
                       )}
                    </td>
                    <td className="py-4 px-5 text-center">
                       <span className="inline-flex items-center px-2 py-1 rounded border border-orange-200 bg-orange-50 text-xs font-semibold text-orange-600">
                          待处理
                       </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-2">
      {/* 搜索框 */}
      <div className="relative mb-6">
        <input
          type="text"
          placeholder="搜索问题或规则编号..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full px-5 py-3 pl-12 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent shadow-sm transition-shadow hover:shadow text-sm"
        />
        <svg className="w-5 h-5 text-gray-400 absolute left-4 top-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
      </div>

      <div className="space-y-0">
        {/* Render defined categories */}
        {CATEGORIES.map(cat => renderCategoryTable(cat.id, cat.name, cat.description, grouped[cat.id], true))}

        {/* Render AI if exists */}
        {renderCategoryTable("ai", "AI 智能分析", "由大模型定位提取的关键问题和差异", grouped["ai"], false)}

        {/* Render Other if exists */}
        {renderCategoryTable("other", "其他检查项", "未分配至特定类别的检查结果", grouped["other"], false)}
      </div>

      {issues.length === 0 && (
        <div className="text-center py-16 bg-white rounded-2xl border border-dashed border-gray-300 shadow-sm">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-green-50 text-green-500 mb-6 border-8 border-green-50/50">
            <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h3 className="text-xl font-bold text-gray-900">恭喜！查重比对通过</h3>
          <p className="text-gray-500 mt-2 text-sm">当前条件过滤下，未发现异常问题记录</p>
        </div>
      )}
    </div>
  );
}
