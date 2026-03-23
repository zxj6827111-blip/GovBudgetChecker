"use client";

import { useMemo, useState } from "react";

import IssueCard from "./IssueCard";
import IssueList from "./IssueList";

export type IssueItem = {
  id: string;
  source: "ai" | "rule";
  rule_id?: string;
  severity: "info" | "low" | "medium" | "high" | "critical" | "manual_review";
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
    field?: string;
    code?: string;
    subject?: string;
    page?: number;
    pages?: number[];
    table_refs?: Array<Record<string, any>>;
  };
  metrics: Record<string, any>;
  suggestion?: string;
  tags: string[];
  created_at: number;
  job_id?: string;
  page_number?: number;
  bbox?: number[];
  amount?: number;
  percentage?: number;
  text_snippet?: string;
  why_not?: string;
  display?: {
    summary?: string;
    page_text?: string;
    location_text?: string;
    detail_lines?: string[];
    evidence_text?: string;
  };
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
  merged_ids?: string[];
};

export type DualModeResult = {
  ai_findings: IssueItem[];
  rule_findings: IssueItem[];
  merged: MergedSummary;
  meta: {
    elapsed_ms?: Record<string, number>;
    tokens?: Record<string, number>;
    ai_status?: string;
    [key: string]: any;
  };
};

type TabType = "merged" | "ai" | "rule";

interface IssueTabsProps {
  result: DualModeResult;
  onIssueClick?: (issue: IssueItem) => void;
  onIgnoreIssue?: (issue: IssueItem) => void | Promise<void>;
  ignoringIssueId?: string | null;
  job_id?: string;
}

export default function IssueTabs({
  result,
  onIssueClick,
  onIgnoreIssue,
  ignoringIssueId,
  job_id,
}: IssueTabsProps) {
  const [activeTab, setActiveTab] = useState<TabType>("merged");
  const [mergedViewMode, setMergedViewMode] = useState<"cards" | "table">("cards");

  const aiFindings = useMemo(
    () => attachJobId(result.ai_findings || [], job_id),
    [job_id, result.ai_findings]
  );
  const ruleFindings = useMemo(
    () => attachJobId(result.rule_findings || [], job_id),
    [job_id, result.rule_findings]
  );
  const mergedIssues = useMemo(
    () => projectMergedIssues(aiFindings, ruleFindings, result.merged),
    [aiFindings, result.merged, ruleFindings]
  );

  const mergedTotals = result.merged?.totals || {
    ai: aiFindings.length,
    rule: ruleFindings.length,
    merged: mergedIssues.length,
    conflicts: 0,
    agreements: 0,
  };

  const tabs: Array<{ id: TabType; label: string; count: number; color: string }> = [
    {
      id: "merged",
      label: "合并视图",
      count: mergedTotals.merged || mergedIssues.length,
      color: "bg-blue-100 text-blue-800",
    },
    {
      id: "ai",
      label: "AI 检查",
      count: aiFindings.length,
      color: "bg-emerald-100 text-emerald-800",
    },
    {
      id: "rule",
      label: "本地规则",
      count: ruleFindings.length,
      color: "bg-violet-100 text-violet-800",
    },
  ];

  const renderCards = (issues: IssueItem[], showSource = false) => {
    if (issues.length === 0) {
      if (activeTab === "ai") {
        const aiStatus = result.meta?.ai_status;
        if (aiStatus === "processing") {
          return <StatusNotice title="AI 正在分析" description="结果尚未返回，稍后刷新即可看到 AI 问题。" tone="info" />;
        }
        if (aiStatus === "fallback") {
          return <StatusNotice title="AI 未返回结果" description="当前任务已回退为本地规则结果，AI 问题列表为空。" tone="warn" />;
        }
      }
      return <EmptyIssues />;
    }

    return (
      <div className="space-y-5">
        {issues.map((issue) => (
          <IssueCard
            key={issue.id}
            issue={issue}
            showSource={showSource}
            onClick={() => onIssueClick?.(issue)}
            onIgnore={onIgnoreIssue}
            isIgnoring={ignoringIssueId === issue.id}
          />
        ))}
      </div>
    );
  };

  const renderMergedView = () => {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 md:grid-cols-4 lg:grid-cols-5">
          <SummaryCard title="合并问题 (去重)" value={String(mergedTotals.merged || mergedIssues.length)} tone="blue" />
          <SummaryCard title="来源命中 (AI)" value={String(mergedTotals.ai || aiFindings.length)} tone="green" />
          <SummaryCard title="来源命中 (本地)" value={String(mergedTotals.rule || ruleFindings.length)} tone="violet" />
          <SummaryCard title="智能去重/冲突" value={String(mergedTotals.conflicts || 0)} tone="slate" />
          <SummaryCard title="已忽略" value={String(result.meta?.ignored_count || 0)} tone="rose" />
        </div>

        {(result.merged?.conflicts?.length || result.merged?.agreements?.length || result.meta?.ignored_count > 0) && (
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-3 text-base font-semibold text-slate-900">合并摘要</div>
            {result.merged?.conflicts?.length ? (
              <div className="mb-3 rounded-xl border border-rose-100 bg-rose-50 p-3 text-sm text-rose-800">
                检测到 {result.merged.conflicts.length} 条 AI 与规则之间的冲突项。
              </div>
            ) : null}
            {result.merged?.agreements?.length ? (
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-3 text-sm text-emerald-800">
                检测到 {result.merged.agreements.length} 条 AI 与规则一致的问题。
              </div>
            ) : null}
          </div>
        )}

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-base font-semibold text-slate-900">问题明细</div>
              <div className="mt-1 text-sm text-slate-500">
                可直接查看页码、定位信息、截图预览，并对 AI 或本地规则命中执行忽略。
              </div>
            </div>
            <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1">
              <button
                type="button"
                onClick={() => setMergedViewMode("cards")}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  mergedViewMode === "cards"
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                截图卡片
              </button>
              <button
                type="button"
                onClick={() => setMergedViewMode("table")}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  mergedViewMode === "table"
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                分类清单
              </button>
            </div>
          </div>

          {mergedViewMode === "cards" ? (
            renderCards(mergedIssues, true)
          ) : (
            <IssueList
              issues={mergedIssues}
              onIssueClick={onIssueClick}
              onIgnoreIssue={onIgnoreIssue}
              ignoringIssueId={ignoringIssueId}
              showSource={true}
              title="所有问题分类清单"
            />
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="w-full">
      <div className="border-b border-slate-200">
        <nav className="-mb-px flex flex-wrap gap-6">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`border-b-2 px-1 py-3 text-sm font-medium whitespace-nowrap ${
                activeTab === tab.id
                  ? "border-indigo-500 text-indigo-600"
                  : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"
              }`}
            >
              {tab.label}
              <span
                className={`ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tab.color}`}
              >
                {tab.count}
              </span>
            </button>
          ))}
        </nav>
      </div>

      <div className="mt-6">
        {activeTab === "merged" && renderMergedView()}
        {activeTab === "ai" && renderCards(aiFindings)}
        {activeTab === "rule" && renderCards(ruleFindings)}
      </div>
    </div>
  );
}

function attachJobId(issues: IssueItem[], jobId?: string): IssueItem[] {
  if (!jobId) return issues;
  return issues.map((issue) => {
    if (issue.job_id === jobId) return issue;
    return {
      ...issue,
      job_id: jobId,
    };
  });
}

function projectMergedIssues(
  aiFindings: IssueItem[],
  ruleFindings: IssueItem[],
  merged?: MergedSummary
): IssueItem[] {
  const dedupedSourceIssues = dedupeIssuesById([...aiFindings, ...ruleFindings]).sort(compareIssues);
  const mergedIds = Array.isArray(merged?.merged_ids)
    ? merged.merged_ids.map((id: string) => String(id || "").trim()).filter(Boolean)
    : [];

  if (!mergedIds.length) {
    return dedupedSourceIssues;
  }

  const issueById = new Map<string, IssueItem>();
  for (const issue of dedupedSourceIssues) {
    const issueId = String(issue.id || "").trim();
    if (issueId && !issueById.has(issueId)) {
      issueById.set(issueId, issue);
    }
  }

  const projected: IssueItem[] = [];
  const seen = new Set<string>();
  for (const mergedId of mergedIds) {
    if (seen.has(mergedId)) continue;
    const issue = issueById.get(mergedId);
    if (!issue) continue;
    seen.add(mergedId);
    projected.push(issue);
  }

  return projected.length ? projected : dedupedSourceIssues;
}

function dedupeIssuesById(issues: IssueItem[]): IssueItem[] {
  const deduped: IssueItem[] = [];
  const seen = new Set<string>();
  for (const issue of issues) {
    const issueId = String(issue.id || "").trim();
    if (issueId) {
      if (seen.has(issueId)) continue;
      seen.add(issueId);
    }
    deduped.push(issue);
  }
  return deduped;
}

function compareIssues(left: IssueItem, right: IssueItem) {
  const severityDiff = severityRank(right.severity) - severityRank(left.severity);
  if (severityDiff !== 0) return severityDiff;
  return Number(right.created_at || 0) - Number(left.created_at || 0);
}

function severityRank(severity: IssueItem["severity"]) {
  const rank = {
    critical: 5,
    high: 4,
    manual_review: 3.5,
    medium: 3,
    low: 2,
    info: 1,
  };
  return rank[severity] || 0;
}

function SummaryCard({
  title,
  value,
  tone,
}: {
  title: string;
  value: string;
  tone: "blue" | "green" | "violet" | "rose" | "slate";
}) {
  const toneClasses = {
    blue: "border-blue-100 bg-blue-50 text-blue-900",
    green: "border-emerald-100 bg-emerald-50 text-emerald-900",
    violet: "border-violet-100 bg-violet-50 text-violet-900",
    rose: "border-rose-100 bg-rose-50 text-rose-900",
    slate: "border-slate-200 bg-slate-50 text-slate-700",
  };

  return (
    <div className={`rounded-2xl border p-4 shadow-sm ${toneClasses[tone]}`}>
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-2 text-2xl font-bold">{value}</div>
    </div>
  );
}

function StatusNotice({
  title,
  description,
  tone,
}: {
  title: string;
  description: string;
  tone: "info" | "warn";
}) {
  const toneClasses = {
    info: "border-blue-100 bg-blue-50 text-blue-900",
    warn: "border-amber-100 bg-amber-50 text-amber-900",
  };

  return (
    <div className={`rounded-2xl border p-5 shadow-sm ${toneClasses[tone]}`}>
      <div className="text-base font-semibold">{title}</div>
      <div className="mt-1 text-sm">{description}</div>
    </div>
  );
}

function EmptyIssues() {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white py-16 text-center shadow-sm">
      <div className="text-lg font-semibold text-slate-900">当前暂无问题</div>
      <p className="mt-2 text-sm text-slate-500">可以切换标签页，或者稍后刷新查看最新分析结果。</p>
    </div>
  );
}
