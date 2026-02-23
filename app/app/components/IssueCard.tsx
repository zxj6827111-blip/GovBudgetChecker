"use client";

import { IssueItem } from "./IssueTabs";

interface IssueCardProps {
  issue: IssueItem;
  onClick?: () => void;
  showSource?: boolean;
  compact?: boolean;
}

export default function IssueCard({
  issue,
  onClick,
  showSource = false,
  compact = false
}: IssueCardProps) {
  const getSeverityColor = (severity: string) => {
    const colors = {
      critical: "border-red-500 bg-red-50",
      high: "border-red-400 bg-red-50",
      medium: "border-yellow-400 bg-yellow-50",
      low: "border-blue-400 bg-blue-50",
      info: "border-gray-400 bg-gray-50",
    };
    return colors[severity as keyof typeof colors] || colors.info;
  };

  const getSeverityBadge = (severity: string) => {
    const colors = {
      critical: "bg-red-100 text-red-800 border-red-200",
      high: "bg-red-100 text-red-800 border-red-200",
      medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
      low: "bg-blue-100 text-blue-800 border-blue-200",
      info: "bg-gray-100 text-gray-800 border-gray-200",
    };
    return colors[severity as keyof typeof colors] || colors.info;
  };

  const getSourceBadge = (source: string) => {
    return source === "ai"
      ? "bg-green-100 text-green-800 border-green-200"
      : "bg-purple-100 text-purple-800 border-purple-200";
  };

  const getSeverityText = (severity: string) => {
    const texts = {
      critical: "严重",
      high: "高",
      medium: "中",
      low: "低",
      info: "信息",
    };
    return texts[severity as keyof typeof texts] || severity;
  };

  const getSeverityIcon = (severity: string) => {
    switch (severity) {
      case "critical":
        return (
          <svg className="w-4 h-4 text-red-600" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
        );
      case "high":
        return (
          <svg className="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
        );
      case "medium":
        return (
          <svg className="w-4 h-4 text-yellow-600" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
        );
      case "low":
        return (
          <svg className="w-4 h-4 text-blue-600" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
        );
      default:
        return (
          <svg className="w-4 h-4 text-gray-600" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
        );
    }
  };

  if (compact) {
    return (
      <div
        className={`border-l-4 ${getSeverityColor(issue.severity)} p-3 cursor-pointer hover:shadow-sm transition-shadow`}
        onClick={onClick}
      >
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <div className="flex items-center space-x-2 mb-1">
              {getSeverityIcon(issue.severity)}
              <span className="text-sm font-medium text-gray-900 truncate">
                {issue.title}
              </span>
              {issue.rule_id && (
                <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                  {issue.rule_id}
                </span>
              )}
            </div>
            <p className="text-xs text-gray-600 line-clamp-2">{issue.message}</p>
          </div>
          {issue.location.page && (
            <span className="text-xs text-gray-500 ml-2 flex-shrink-0">
              P{issue.location.page}
            </span>
          )}
        </div>
      </div>
    );
  }

  // Determine if we should show description (hide if same as title)
  const showDescription = issue.message && issue.message !== issue.title;

  return (
    <div
      className={`border rounded-lg p-4 cursor-pointer hover:shadow-md transition-all duration-200 bg-white group ${onClick ? "hover:border-indigo-300" : ""
        }`}
      onClick={onClick}
    >
      {/* 头部 */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-2 flex-wrap">
          {showSource && (
            <span
              className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium border ${getSourceBadge(
                issue.source
              )}`}
            >
              {issue.source === "ai" ? "AI" : "规则"}
            </span>
          )}
          <span
            className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium border ${getSeverityBadge(
              issue.severity
            )}`}
          >
            {getSeverityIcon(issue.severity)}
            <span className="ml-1">{getSeverityText(issue.severity)}</span>
          </span>
          {issue.rule_id && (
            <span className="inline-flex items-center px-2 py-1 rounded-full text-xs bg-gray-100 text-gray-700 border border-gray-200">
              {issue.rule_id}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2 text-xs text-gray-500">
          {issue.location.page && (
            <a
              href={issue.job_id ? `/api/files/${issue.job_id}/source#page=${issue.location.page}` : "#"}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => issue.job_id && e.stopPropagation()}
              className={`flex items-center space-x-1 px-2 py-1 rounded border transition-colors ${issue.job_id
                ? "bg-slate-100 hover:bg-slate-200 text-slate-700 border-slate-200 hover:border-slate-300"
                : "bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed"
                }`}
              title={issue.job_id ? "在新标签页中打开PDF并跳转到此页" : "无法获取文件链接"}
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
              <span>第 {issue.location.page} 页</span>
            </a>
          )}
          <span>{new Date(issue.created_at * 1000).toLocaleString()}</span>
        </div>
      </div>

      {/* 标题和描述 */}
      <h4 className="font-semibold text-gray-900 mb-2 text-base leading-tight">
        {issue.title}
      </h4>
      {showDescription && (
        <p className="text-sm text-gray-600 mb-3 leading-relaxed">{issue.message}</p>
      )}

      {/* 位置信息 (只显示除页码外的其他定位) */}
      {(issue.location.section || issue.location.table || issue.location.row || issue.location.col) && (
        <div className="bg-gray-50 rounded p-2 mb-3">
          <div className="text-xs text-gray-600">
            <span className="font-medium mr-1">定位：</span>
            {issue.location.section && <span className="bg-white border border-gray-200 rounded px-1.5 py-0.5 mr-2">{issue.location.section}</span>}
            {issue.location.table && (
              <span className="bg-white border border-gray-200 rounded px-1.5 py-0.5 mr-2">
                {issue.location.table}
              </span>
            )}
            {(issue.location.row || issue.location.col) && (
              <span className="text-gray-500">
                {issue.location.row && `行: ${issue.location.row}`}
                {issue.location.row && issue.location.col && ", "}
                {issue.location.col && `列: ${issue.location.col}`}
              </span>
            )}
          </div>
        </div>
      )}

      {/* 指标信息 */}
      {Object.keys(issue.metrics).length > 0 && (
        <div className="bg-blue-50 rounded p-2 mb-3">
          <div className="text-xs text-blue-800">
            <span className="font-medium">相关指标：</span>
            <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1">
              {Object.entries(issue.metrics).map(([key, value]) => (
                <div key={key} className="flex justify-between">
                  <span className="text-blue-600 opacity-80">{key}:</span>
                  <span className="font-mono font-medium">
                    {typeof value === 'number'
                      ? value.toLocaleString()
                      : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* 证据 */}
      {issue.evidence.length > 0 && (
        <div className="border-l-4 border-yellow-400 pl-3 mb-3 bg-yellow-50/50 py-1 rounded-r">
          <div className="text-xs text-slate-500 mb-1 flex items-center">
            <svg className="w-3 h-3 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <span className="font-medium">证据片段：</span>
          </div>
          {issue.evidence.slice(0, 2).map((evidence, idx) => {
            // Hide evidence if it's too similar to title or message to capture duplication
            // Ensure we have some text to show, fallback to empty string
            const txt = evidence.text || (evidence as any).text_snippet || "";
            // if (!txt || txt === issue.title || txt === issue.message) return null; // Logic removed: user wants to see evidence regardless

            return (
              <div key={idx} className="mb-2 last:mb-0 group/evidence relative">
                <div className="text-sm text-gray-700 italic bg-white border border-yellow-100 p-2 rounded shadow-sm">
                  &quot;{txt}&quot;
                </div>
                {issue.job_id && evidence.page && (
                  <a
                    href={`/api/files/${issue.job_id}/source#page=${evidence.page}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="absolute top-1 right-1 opacity-0 group-hover/evidence:opacity-100 bg-white text-indigo-600 text-[10px] px-1.5 py-0.5 rounded border border-indigo-100 hover:text-indigo-800 transition-opacity"
                  >
                    跳转 P{evidence.page} ↗
                  </a>
                )}
              </div>
            );
          })}
          {issue.evidence.length > 2 && (
            <div className="text-xs text-gray-500 mt-1">
              还有 {issue.evidence.length - 2} 条证据...
            </div>
          )}
        </div>
      )}

      {/* 建议 */}
      {issue.suggestion && (
        <div className="bg-green-50 border border-green-200 rounded p-3">
          <div className="flex items-start">
            <svg className="w-4 h-4 text-green-600 mt-0.5 mr-2 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div>
              <div className="text-xs font-medium text-green-800 mb-0.5">建议：</div>
              <div className="text-sm text-green-700">{issue.suggestion}</div>
            </div>
          </div>
        </div>
      )}

      {/* 标签 */}
      {issue.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-3 pt-2 border-t border-gray-100">
          {issue.tags.map((tag, idx) => (
            <span
              key={idx}
              className="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
