"use client";

import { useState } from "react";
import IssueList from "./IssueList";

export type IssueItem = {
  id: string;
  source: "ai" | "rule";
  rule_id?: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  title: string;
  message: string;
  evidence: Array<{
    page: number;
    text?: string;
    text_snippet?: string;
    bbox?: number[];
  }>;
  location: {
    section?: string;
    table?: string;
    row?: string;
    col?: string;
    page?: number;
  };
  metrics: Record<string, any>;
  suggestion?: string;
  tags: string[];
  created_at: number;
  job_id?: string;
  // Extra fields from page.tsx usage
  page_number?: number;
  bbox?: number[];
  amount?: number;
  percentage?: number;
  text_snippet?: string;
  why_not?: string;
};

export type ConflictItem = {
  key: string;
  ai_issue?: string;
  rule_issue?: string;
  reason: "value-mismatch" | "missing" | "page-mismatch";
};

export type MergedSummary = {
  totals: {
    ai: number;
    rule: number;
    merged: number;
    conflicts: number;
    agreements: number;
  };
  conflicts: ConflictItem[];
  agreements: string[];
};

export type DualModeResult = {
  ai_findings: IssueItem[];
  rule_findings: IssueItem[];
  merged: MergedSummary;
  meta: {
    elapsed_ms?: Record<string, number>;
    tokens?: Record<string, number>;
    [key: string]: any;
  };
};

type TabType = "ai" | "rule" | "merged";

interface IssueTabsProps {
  result: DualModeResult;
  onIssueClick?: (issue: IssueItem) => void;
  job_id?: string;
}

export default function IssueTabs({ result, onIssueClick, job_id }: IssueTabsProps) {
  const [activeTab, setActiveTab] = useState<TabType>("merged");

  // Inject job_id into issues if provided
  const { ai_findings, rule_findings, merged } = result;
  if (job_id) {
    ai_findings.forEach(i => i.job_id = job_id);
    rule_findings.forEach(i => i.job_id = job_id);
    // Merged issues might already reference these objects, but to be sure:
    // We modify the objects in place, so it should propagate.
  }

  const tabs = [
    {
      id: "merged" as TabType,
      label: "合并视图",
      count: merged?.totals?.merged || 0,
      color: "bg-blue-100 text-blue-800",
    },
    {
      id: "ai" as TabType,
      label: "AI 检查",
      count: ai_findings.length,
      color: "bg-green-100 text-green-800",
    },
    {
      id: "rule" as TabType,
      label: "本地规则",
      count: rule_findings.length,
      color: "bg-purple-100 text-purple-800",
    },
  ];

  const getSeverityBadge = (severity: string) => {
    const colors = {
      critical: "bg-red-100 text-red-800",
      high: "bg-red-100 text-red-800",
      medium: "bg-yellow-100 text-yellow-800",
      low: "bg-blue-100 text-blue-800",
      info: "bg-gray-100 text-gray-800",
    };
    return colors[severity as keyof typeof colors] || colors.info;
  };

  const getSourceBadge = (source: string) => {
    return source === "ai"
      ? "bg-green-100 text-green-800"
      : "bg-purple-100 text-purple-800";
  };

  const renderIssueList = (issues: IssueItem[], showSource = false) => {
    if (issues.length === 0) {
      // 检查是否为AI结果为空且服务降级的情况
      if (showSource === false && activeTab === "ai") {
        // 检查 AI 是否仍在处理中
        const aiStatus = result.meta?.ai_status;
        if (aiStatus === "processing") {
          return (
            <div className="text-center py-8">
              <div className="w-16 h-16 mx-auto mb-4">
                <svg className="animate-spin h-16 w-16 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              </div>
              <p className="text-lg font-medium text-blue-600">AI 正在解析中，请耐心等待</p>
            </div>
          );
        }

        // AI标签页的空态提示
        const isAiFallback = result.meta?.provider_stats?.some((stat: any) => stat.fell_back === true);
        if (isAiFallback) {
          return (
            <div className="text-center py-8 text-amber-600">
              <div className="w-16 h-16 mx-auto mb-4 text-amber-400">
                <svg fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
              </div>
              <p className="text-lg font-medium">AI 服务降级</p>
              <p className="text-sm">AI 检测服务暂时不可用，已降级到本地规则检测</p>
            </div>
          );
        } else {
          return (
            <div className="text-center py-8 text-gray-500">
              <div className="w-16 h-16 mx-auto mb-4 text-gray-300">
                <svg fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              </div>
              <p className="text-lg font-medium">AI 检测未发现问题</p>
              <p className="text-sm">文档通过AI智能检测，未发现合规性问题</p>
            </div>
          );
        }
      } else {
        return (
          <div className="text-center py-8 text-gray-500">
            <div className="w-16 h-16 mx-auto mb-4 text-gray-300">
              <svg fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
            </div>
            <p className="text-lg font-medium">没有发现问题</p>
            <p className="text-sm">检测通过，未发现合规性问题</p>
          </div>
        );
      }
    }

    return (
      <div className="space-y-4">
        {issues.map((issue) => (
          <div
            key={issue.id}
            className="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"
            onClick={() => onIssueClick?.(issue)}
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center space-x-2">
                {showSource && (
                  <span
                    className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${getSourceBadge(
                      issue.source
                    )}`}
                  >
                    {issue.source === "ai" ? "AI" : "规则"}
                  </span>
                )}
                <span
                  className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${getSeverityBadge(
                    issue.severity
                  )}`}
                >
                  {issue.severity === "critical"
                    ? "严重"
                    : issue.severity === "high"
                      ? "高"
                      : issue.severity === "medium"
                        ? "中"
                        : issue.severity === "low"
                          ? "低"
                          : "信息"}
                </span>
                {issue.rule_id && (
                  <span className="text-xs text-gray-500">{issue.rule_id}</span>
                )}
              </div>
              {issue.location.page && (
                <span className="text-xs text-gray-500">
                  第 {issue.location.page} 页
                </span>
              )}
            </div>

            <h4 className="font-medium text-gray-900 mb-1">{issue.title}</h4>
            <p className="text-sm text-gray-600 mb-2">{issue.message}</p>

            {issue.evidence.length > 0 && (
              <div className="text-xs text-gray-500">
                <span className="font-medium">证据：</span>
                {(issue.evidence[0].text || issue.evidence[0]["text_snippet"] || "").substring(0, 100)}
                {(issue.evidence[0].text || issue.evidence[0]["text_snippet"] || "").length > 100 && "..."}
              </div>
            )}

            {issue.tags.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {issue.tags.map((tag, idx) => (
                  <span
                    key={idx}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-700"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  const renderMergedView = () => {
    const { conflicts = [], agreements = [] } = merged || {};

    // 使用合并后的实际问题列表，而不是简单拼接
    const getMergedIssues = () => {
      const mergedIssues: IssueItem[] = [];
      const usedAiIds = new Set<string>();
      const usedRuleIds = new Set<string>();

      // 添加一致的问题（优先使用AI结果）
      agreements.forEach(agreementId => {
        const aiIssue = ai_findings.find(issue => issue.id === agreementId);
        const ruleIssue = rule_findings.find(issue => issue.id === agreementId);

        if (aiIssue) {
          mergedIssues.push(aiIssue);
          usedAiIds.add(aiIssue.id);
        } else if (ruleIssue) {
          mergedIssues.push(ruleIssue);
          usedRuleIds.add(ruleIssue.id);
        }
      });

      // 添加冲突问题（两个都保留）
      conflicts.forEach(conflict => {
        if (conflict.ai_issue) {
          const aiIssue = ai_findings.find(issue => issue.id === conflict.ai_issue);
          if (aiIssue && !usedAiIds.has(aiIssue.id)) {
            mergedIssues.push(aiIssue);
            usedAiIds.add(aiIssue.id);
          }
        }
        if (conflict.rule_issue) {
          const ruleIssue = rule_findings.find(issue => issue.id === conflict.rule_issue);
          if (ruleIssue && !usedRuleIds.has(ruleIssue.id)) {
            mergedIssues.push(ruleIssue);
            usedRuleIds.add(ruleIssue.id);
          }
        }
      });

      // 添加未匹配的AI问题
      ai_findings.forEach(issue => {
        if (!usedAiIds.has(issue.id)) {
          mergedIssues.push(issue);
        }
      });

      // 添加未匹配的规则问题
      rule_findings.forEach(issue => {
        if (!usedRuleIds.has(issue.id)) {
          mergedIssues.push(issue);
        }
      });

      return mergedIssues;
    };

    const allIssues = getMergedIssues();

    return (
      <div className="space-y-6">
        {/* 统计概览 */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="bg-blue-50 p-3 rounded-lg text-center">
            <div className="text-2xl font-bold text-blue-600">
              {merged?.totals?.merged || 0}
            </div>
            <div className="text-sm text-blue-600">总问题</div>
          </div>
          <div className="bg-green-50 p-3 rounded-lg text-center">
            <div className="text-2xl font-bold text-green-600">
              {merged?.totals?.agreements || 0}
            </div>
            <div className="text-sm text-green-600">一致</div>
          </div>
          <div className="bg-red-50 p-3 rounded-lg text-center">
            <div className="text-2xl font-bold text-red-600">
              {merged?.totals?.conflicts || 0}
            </div>
            <div className="text-sm text-red-600">冲突</div>
          </div>
          <div className="bg-yellow-50 p-3 rounded-lg text-center">
            <div className="text-2xl font-bold text-yellow-600">
              {merged?.totals?.ai || 0}
            </div>
            <div className="text-sm text-yellow-600">AI独有</div>
          </div>
          <div className="bg-purple-50 p-3 rounded-lg text-center">
            <div className="text-2xl font-bold text-purple-600">
              {merged?.totals?.rule || 0}
            </div>
            <div className="text-sm text-purple-600">规则独有</div>
          </div>
        </div>

        {/* 冲突列表 */}
        {conflicts.length > 0 && (
          <div>
            <h3 className="text-lg font-semibold mb-3 flex items-center">
              <span className="w-3 h-3 bg-red-500 rounded-full mr-2"></span>
              冲突项目 ({conflicts.length})
            </h3>
            <div className="space-y-3">
              {conflicts.map((conflict, idx) => {
                const aiIssue = ai_findings.find((i) => i.id === conflict.ai_issue);
                const ruleIssue = rule_findings.find((i) => i.id === conflict.rule_issue);

                return (
                  <div key={idx} className="border border-red-200 rounded-lg p-4 bg-red-50">
                    <div className="flex items-center mb-2">
                      <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800">
                        冲突
                      </span>
                      <span className="ml-2 text-sm text-gray-600">
                        原因: {conflict.reason === "value-mismatch" ? "值不匹配" :
                          conflict.reason === "missing" ? "缺失" : "页码不匹配"}
                      </span>
                    </div>
                    <div className="grid md:grid-cols-2 gap-4">
                      {aiIssue && (
                        <div className="border-l-4 border-green-400 pl-3">
                          <div className="text-sm font-medium text-green-700">AI 检查结果</div>
                          <div className="text-sm text-gray-600">{aiIssue.message}</div>
                        </div>
                      )}
                      {ruleIssue && (
                        <div className="border-l-4 border-purple-400 pl-3">
                          <div className="text-sm font-medium text-purple-700">规则检查结果</div>
                          <div className="text-sm text-gray-600">{ruleIssue.message}</div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 所有问题列表 - 使用新的 IssueList 组件来展示分类 */}
        <div>
          <IssueList
            issues={allIssues}
            onIssueClick={onIssueClick}
            showSource={true}
            title="所有问题分类清单"
          />
        </div>
      </div>
    );
  };

  return (
    <div className="w-full">
      {/* Tab 导航 */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`py-2 px-1 border-b-2 font-medium text-sm whitespace-nowrap ${activeTab === tab.id
                ? "border-indigo-500 text-indigo-600"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                }`}
            >
              {tab.label}
              <span
                className={`ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${tab.color}`}
              >
                {tab.count}
              </span>
            </button>
          ))}
        </nav>
      </div>

      {/* Tab 内容 */}
      <div className="mt-6">
        {activeTab === "merged" && renderMergedView()}
        {activeTab === "ai" && renderIssueList(ai_findings)}
        {activeTab === "rule" && renderIssueList(rule_findings)}
      </div>
    </div>
  );
}