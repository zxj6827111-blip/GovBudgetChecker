"use client";

import { useState, useMemo } from "react";
import { IssueItem } from "./IssueTabs";
import IssueCard from "./IssueCard";

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

  // Initialize expanded state (expand only if has issues initially)
  // We'll rely on the render logic to set defaults effectively or user interaction

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
      // 1. AI Issues
      if (issue.source === "ai") {
        groups["ai"].push(issue);
        return;
      }

      const rid = issue.rule_id || "";
      let matched = false;

      // 2. Logic to match categories
      // Priority 1: Exact matches
      for (const cat of CATEGORIES) {
        if (cat.exactMatches?.includes(rid)) {
          groups[cat.id].push(issue);
          matched = true;
          break;
        }
      }

      // Priority 2: Prefix matches
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

  const renderCategory = (id: string, name: string, description: string, items: IssueItem[], alwaysShow = true) => {
    // If filtering by search, hide empty categories unless they match?
    // User requirement: "如果没有问题的话就直接显示绿色或者打钩" -> implies showing all major categories.
    // So we show all CATEGORIES, plus AI and Other if they have content.

    if (!alwaysShow && items.length === 0) return null;

    const hasIssues = items.length > 0;
    const isExpanded = expandedCats[id] ?? hasIssues; // Default expand if has issues

    return (
      <div key={id} className="border border-gray-200 rounded-lg overflow-hidden mb-4 bg-white shadow-sm">
        <div
          className={`px-4 py-3 flex items-center justify-between cursor-pointer transition-colors ${hasIssues ? "bg-red-50 hover:bg-red-100" : "bg-green-50 hover:bg-green-100"
            }`}
          onClick={() => toggleCat(id)}
        >
          <div className="flex items-center space-x-3">
            {hasIssues ? (
              <div className="flex-shrink-0 w-6 h-6 rounded-full bg-red-100 text-red-600 flex items-center justify-center">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </div>
            ) : (
              <div className="flex-shrink-0 w-6 h-6 rounded-full bg-green-100 text-green-600 flex items-center justify-center">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
            )}
            <div>
              <h4 className={`font-medium ${hasIssues ? "text-red-900" : "text-green-900"}`}>{name}</h4>
              <p className={`text-xs ${hasIssues ? "text-red-700" : "text-green-700"} hidden sm:block`}>{description}</p>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            {hasIssues && (
              <span className="bg-white px-2 py-0.5 rounded text-xs font-bold text-red-600 shadow-sm">
                {items.length} 个问题
              </span>
            )}
            <svg
              className={`w-5 h-5 text-gray-500 transition-transform ${isExpanded ? "transform rotate-180" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {/* Content */}
        {isExpanded && hasIssues && (
          <div className="p-4 bg-gray-50 border-t border-gray-100 space-y-3">
            {items.map(issue => (
              <IssueCard
                key={issue.id}
                issue={issue}
                onClick={() => onIssueClick?.(issue)}
                showSource={showSource}
              />
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* 搜索框 */}
      <div className="relative">
        <input
          type="text"
          placeholder="搜索问题..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full px-4 py-2 pl-10 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <svg className="w-5 h-5 text-gray-400 absolute left-3 top-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
      </div>

      <div className="space-y-4">
        {/* Render defined categories */}
        {CATEGORIES.map(cat => renderCategory(cat.id, cat.name, cat.description, grouped[cat.id], true))}

        {/* Render AI if exists */}
        {renderCategory("ai", "AI 智能分析", "由 AI 大模型发现的潜在问题", grouped["ai"], false)}

        {/* Render Other if exists */}
        {renderCategory("other", "其他检查项", "未分类的规则检查", grouped["other"], false)}
      </div>

      {issues.length === 0 && (
        <div className="text-center py-12 bg-white rounded-lg border border-dashed border-gray-300">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-100 text-green-600 mb-4">
            <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-900">恭喜！文档检查通过</h3>
          <p className="text-gray-500 mt-1">未发现任何合规性问题</p>
        </div>
      )}
    </div>
  );
}