"use client";

import { useMemo, useState } from "react";

import type { IssueItem } from "./IssueTabs";
import { getIssuePresentation } from "../utils/issuePresentation";
import {
  buildIssueViewerUrl,
  buildPdfPageUrl,
  getLocationPreviewRefs,
  getPreviewTarget,
  getPrimaryPage,
} from "../utils/issueViewer";

interface IssueListProps {
  issues: IssueItem[];
  onIssueClick?: (issue: IssueItem) => void;
  onIgnoreIssue?: (issue: IssueItem) => void | Promise<void>;
  ignoringIssueId?: string | null;
  showSource?: boolean;
  title?: string;
}

type CategoryDef = {
  id: string;
  name: string;
  description: string;
  match: (issue: IssueItem) => boolean;
};

const CATEGORIES: CategoryDef[] = [
  {
    id: "basic",
    name: "基础信息合规",
    description: "封面、年度、单位名称等基础信息一致性检查。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-001"]),
  },
  {
    id: "integrity",
    name: "表格完整性",
    description: "检查核心表格、必备章节和目录定位是否完整。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-002"]),
  },
  {
    id: "quality",
    name: "文档质量与规范",
    description: "检查页数、文件大小、空表说明等规范性问题。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-003", "V33-114"]),
  },
  {
    id: "logic",
    name: "表内逻辑关系",
    description: "检查单表内合计、勾稽和计算关系是否成立。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-004", "V33-005"]),
  },
  {
    id: "consistency",
    name: "跨表一致性",
    description: "检查同一指标在不同报表之间是否一致。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-1"]),
  },
  {
    id: "text-data",
    name: "文数一致性",
    description: "检查文字说明与表格数值是否一致。",
    match: (issue) => hasRulePrefix(issue.rule_id, ["V33-2"]),
  },
  {
    id: "ai",
    name: "AI 智能分析",
    description: "大模型识别出的疑似问题。",
    match: (issue) => issue.source === "ai",
  },
];

type GroupedIssues = Record<string, IssueItem[]>;

export default function IssueList({
  issues,
  onIssueClick,
  onIgnoreIssue,
  ignoringIssueId,
  showSource = false,
  title,
}: IssueListProps) {
  const [searchTerm, setSearchTerm] = useState("");

  const filteredIssues = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) return issues;

    return issues.filter((issue) => {
      const presentation = getIssuePresentation(issue);
      const pages = getPageSummary(issue);
      const fields = [
        issue.rule_id,
        issue.title,
        issue.message,
        issue.suggestion,
        issue.location?.table,
        issue.location?.section,
        issue.location?.row,
        issue.location?.field,
        issue.location?.code,
        issue.location?.subject,
        presentation.summary,
        presentation.locationText,
        presentation.evidenceText,
        pages,
      ];

      return fields.some((value) => String(value || "").toLowerCase().includes(keyword));
    });
  }, [issues, searchTerm]);

  const grouped = useMemo(() => {
    const result: GroupedIssues = {
      basic: [],
      integrity: [],
      quality: [],
      logic: [],
      consistency: [],
      "text-data": [],
      ai: [],
      other: [],
    };

    filteredIssues.forEach((issue) => {
      const category = CATEGORIES.find((item) => item.match(issue));
      if (category) {
        result[category.id].push(issue);
      } else {
        result.other.push(issue);
      }
    });

    return result;
  }, [filteredIssues]);

  const sections = [
    ...CATEGORIES.map((category) => ({
      ...category,
      issues: grouped[category.id] || [],
    })),
    {
      id: "other",
      name: "其他问题",
      description: "未归类到固定规则类别的问题。",
      issues: grouped.other || [],
    },
  ].filter((section) => section.issues.length > 0);

  return (
    <div className="space-y-4">
      {title && <div className="text-sm font-medium text-slate-500">{title}</div>}

      <div className="relative">
        <input
          type="text"
          placeholder="搜索问题、规则编号、页码、表格、字段..."
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          className="w-full rounded-xl border border-slate-200 px-4 py-3 pl-11 text-sm shadow-sm transition-shadow hover:shadow focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        />
        <svg
          className="absolute left-4 top-3.5 h-5 w-5 text-slate-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
      </div>

      {sections.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-4">
          {sections.map((section) => (
            <section key={section.id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-100 bg-slate-50 px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-base font-semibold text-slate-900">{section.name}</div>
                    <div className="mt-1 text-sm text-slate-500">{section.description}</div>
                  </div>
                  <span className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-sm font-medium text-slate-600">
                    {section.issues.length} 条
                  </span>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200">
                  <thead className="bg-white">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                        问题
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                        定位
                      </th>
                      {showSource && (
                        <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                          来源
                        </th>
                      )}
                      <th className="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wide text-slate-500">
                        处理
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {section.issues.map((issue) => {
                      const presentation = getIssuePresentation(issue);
                      const previewTarget = getPreviewTarget(issue);
                      const primaryPage = getPrimaryPage(issue);
                      const pageHref = buildPdfPageUrl(issue.job_id, primaryPage);
                      const viewerHref = buildIssueViewerUrl(issue, previewTarget, {
                        title: presentation.summary,
                        location: presentation.locationText,
                      });
                      const refs = getLocationPreviewRefs(issue);
                      const refSummary = refs
                        .slice(0, 2)
                        .map((ref) => ref.locationText)
                        .join("；");

                      return (
                        <tr
                          key={issue.id}
                          className={onIssueClick ? "cursor-pointer hover:bg-slate-50" : undefined}
                          onClick={() => onIssueClick?.(issue)}
                        >
                          <td className="px-4 py-4 align-top">
                            <div className="space-y-2">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${getSeverityBadge(issue.severity)}`}>
                                  {getSeverityText(issue.severity)}
                                </span>
                                {issue.rule_id && (
                                  <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                                    {issue.rule_id}
                                  </span>
                                )}
                                {presentation.pageText && (
                                  <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
                                    {presentation.pageText}
                                  </span>
                                )}
                              </div>
                              <div className="text-sm font-semibold text-slate-900">{presentation.summary}</div>
                              {presentation.detailLines[0] && (
                                <div className="text-sm leading-6 text-slate-600">
                                  {presentation.detailLines[0]}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-4 align-top">
                            <div className="space-y-2 text-sm text-slate-700">
                              <div>{presentation.locationText || "未提取到结构化定位"}</div>
                              {refSummary && (
                                <div className="text-xs leading-5 text-slate-500">补充定位：{refSummary}</div>
                              )}
                              <div className="flex flex-wrap gap-3 text-xs">
                                {viewerHref && (
                                  <a
                                    href={viewerHref}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(event) => event.stopPropagation()}
                                    className="text-emerald-700 hover:text-emerald-900"
                                  >
                                    高亮查看
                                  </a>
                                )}
                                {pageHref && (
                                  <a
                                    href={pageHref}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(event) => event.stopPropagation()}
                                    className="text-indigo-600 hover:text-indigo-800"
                                  >
                                    打开 PDF
                                  </a>
                                )}
                              </div>
                            </div>
                          </td>
                          {showSource && (
                            <td className="px-4 py-4 align-top">
                              <span className={`inline-flex items-center rounded-full border px-2 py-1 text-xs font-medium ${getSourceBadge(issue.source)}`}>
                                {issue.source === "ai" ? "AI" : "本地规则"}
                              </span>
                            </td>
                          )}
                          <td className="px-4 py-4 text-center align-top">
                            <div className="flex flex-col items-center gap-2">
                              <span className="inline-flex items-center rounded-md border border-orange-200 bg-orange-50 px-2 py-1 text-xs font-semibold text-orange-600">
                                待处理
                              </span>
                              {issue.source === "ai" && onIgnoreIssue && (
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    onIgnoreIssue(issue);
                                  }}
                                  disabled={ignoringIssueId === issue.id}
                                  className="inline-flex items-center rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-xs font-medium text-rose-700 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  {ignoringIssueId === issue.id ? "忽略中..." : "忽略"}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white py-16 text-center shadow-sm">
      <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-emerald-50 text-emerald-500">
        <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <h3 className="text-lg font-semibold text-slate-900">当前筛选条件下未发现问题</h3>
      <p className="mt-2 text-sm text-slate-500">可以修改搜索关键词，或者切换到其他结果视图继续查看。</p>
    </div>
  );
}

function hasRulePrefix(ruleId: string | undefined, prefixes: string[]) {
  const normalized = String(ruleId || "").trim().toUpperCase();
  if (!normalized) return false;
  return prefixes.some((prefix) => normalized.startsWith(prefix.toUpperCase()));
}

function getPageSummary(issue: IssueItem) {
  const pages = Array.isArray(issue.location?.pages) ? issue.location.pages : [];
  const page = issue.location?.page || issue.evidence?.[0]?.page || issue.page_number;
  const values = [...pages, page]
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0);
  const uniquePages = Array.from(new Set(values.map((value) => Math.floor(value))));
  return uniquePages.length > 0 ? `第 ${uniquePages.join("、")} 页` : "";
}

function getSeverityBadge(severity: string) {
  const colors = {
    critical: "border-red-200 bg-red-100 text-red-700",
    high: "border-red-200 bg-red-100 text-red-700",
    medium: "border-amber-200 bg-amber-100 text-amber-700",
    low: "border-sky-200 bg-sky-100 text-sky-700",
    info: "border-slate-200 bg-slate-100 text-slate-700",
  };
  return colors[severity as keyof typeof colors] || colors.info;
}

function getSeverityText(severity: string) {
  const texts = {
    critical: "严重",
    high: "高",
    medium: "中",
    low: "低",
    info: "提示",
  };
  return texts[severity as keyof typeof texts] || severity;
}

function getSourceBadge(source: string) {
  return source === "ai"
    ? "border-emerald-200 bg-emerald-100 text-emerald-700"
    : "border-violet-200 bg-violet-100 text-violet-700";
}
