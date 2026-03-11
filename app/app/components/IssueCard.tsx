"use client";

import Image from "next/image";

import type { IssueItem } from "./IssueTabs";
import { getIssuePresentation } from "../utils/issuePresentation";
import {
  buildIssueViewerUrl,
  buildPdfPageUrl,
  buildPreviewUrl,
  getLocationPreviewRefs,
  getPreviewTarget,
  getPrimaryPage,
} from "../utils/issueViewer";

interface IssueCardProps {
  issue: IssueItem;
  onClick?: () => void;
  onIgnore?: (issue: IssueItem) => void | Promise<void>;
  isIgnoring?: boolean;
  showSource?: boolean;
  compact?: boolean;
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

function formatMetricValue(value: unknown) {
  if (typeof value === "number") {
    return value.toLocaleString("zh-CN", {
      maximumFractionDigits: 2,
    });
  }
  return String(value);
}

function PreviewImage({
  src,
  alt,
  maxHeightClass,
}: {
  src: string;
  alt: string;
  maxHeightClass: string;
}) {
  return (
    <Image
      src={src}
      alt={alt}
      width={1600}
      height={1200}
      unoptimized
      className={`block h-auto w-full object-contain bg-white ${maxHeightClass}`}
    />
  );
}

export default function IssueCard({
  issue,
  onClick,
  onIgnore,
  isIgnoring = false,
  showSource = false,
  compact = false,
}: IssueCardProps) {
  const presentation = getIssuePresentation(issue);
  const primaryPage = getPrimaryPage(issue);
  const pageHref = buildPdfPageUrl(issue.job_id, primaryPage);
  const previewTarget = getPreviewTarget(issue);
  const locationPreviewRefs = getLocationPreviewRefs(issue);
  const previewUrl = buildPreviewUrl(issue, previewTarget);
  const viewerHref = buildIssueViewerUrl(issue, previewTarget, {
    title: presentation.summary,
    location: presentation.locationText,
  });
  const canIgnore = issue.source === "ai" && typeof onIgnore === "function";

  if (compact) {
    return (
      <div
        className="cursor-pointer rounded-xl border border-slate-200 bg-white p-3 transition-shadow hover:shadow-sm"
        onClick={onClick}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${getSeverityBadge(
                  issue.severity
                )}`}
              >
                {getSeverityText(issue.severity)}
              </span>
              {issue.rule_id && (
                <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {issue.rule_id}
                </span>
              )}
            </div>
            <p className="truncate text-sm font-medium text-slate-900">{presentation.summary}</p>
            {presentation.detailLines[0] && (
              <p className="mt-1 line-clamp-2 text-xs text-slate-600">{presentation.detailLines[0]}</p>
            )}
          </div>
          {presentation.pageText && (
            <span className="shrink-0 text-xs text-slate-500">{presentation.pageText}</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition-all ${
        onClick ? "cursor-pointer hover:border-indigo-300 hover:shadow-md" : ""
      }`}
      onClick={onClick}
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {showSource && (
              <span
                className={`inline-flex items-center rounded-full border px-2 py-1 text-xs font-medium ${getSourceBadge(
                  issue.source
                )}`}
              >
                {issue.source === "ai" ? "AI" : "本地规则"}
              </span>
            )}
            <span
              className={`inline-flex items-center rounded-full border px-2 py-1 text-xs font-semibold ${getSeverityBadge(
                issue.severity
              )}`}
            >
              {getSeverityText(issue.severity)}
            </span>
            {issue.rule_id && (
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
                {issue.rule_id}
              </span>
            )}
            {presentation.pageText && (
              <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
                {presentation.pageText}
              </span>
            )}
          </div>

          <h3 className="text-base font-semibold text-slate-900">{presentation.summary}</h3>

          {presentation.locationText && (
            <p className="mt-2 text-sm text-slate-600">{presentation.locationText}</p>
          )}
        </div>

        {canIgnore && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onIgnore?.(issue);
            }}
            disabled={isIgnoring}
            className="inline-flex shrink-0 items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-700 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isIgnoring ? "忽略中..." : "忽略此问题"}
          </button>
        )}
      </div>

      {presentation.detailLines.length > 0 && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="mb-2 text-sm font-medium text-slate-800">问题详情</div>
          <ul className="space-y-1.5 text-sm leading-6 text-slate-700">
            {presentation.detailLines.slice(0, 6).map((line) => (
              <li key={line} className="flex gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {Object.keys(issue.metrics || {}).length > 0 && (
        <div className="mb-4 rounded-xl border border-blue-100 bg-blue-50 p-3">
          <div className="mb-2 text-sm font-medium text-blue-900">关键指标</div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(issue.metrics || {})
              .slice(0, 6)
              .map(([key, value]) => (
                <div key={key} className="rounded-lg bg-white/70 px-3 py-2">
                  <div className="text-xs uppercase tracking-wide text-blue-500">{key}</div>
                  <div className="mt-1 text-sm font-medium text-blue-900">
                    {formatMetricValue(value)}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {locationPreviewRefs.length > 0 && (
        <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50 p-3">
          <div className="mb-2 text-sm font-medium text-indigo-900">自动定位结果</div>
          <div className="space-y-3">
            {locationPreviewRefs.map((ref) => {
              const refPageHref = buildPdfPageUrl(issue.job_id, ref.page);
              const refPreviewUrl = buildPreviewUrl(issue, ref.target);
              const refViewerHref = buildIssueViewerUrl(issue, ref.target, {
                title: presentation.summary,
                location: ref.locationText,
              });

              return (
                <div key={ref.key} className="rounded-xl border border-indigo-100 bg-white p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="inline-flex items-center rounded-full border border-indigo-200 bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                          {ref.role}
                        </span>
                        <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs text-slate-600">
                          第 {ref.page} 页
                        </span>
                        {ref.bbox && (
                          <span className="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-xs text-rose-700">
                            已定位红框
                          </span>
                        )}
                      </div>
                      <div className="mt-2 text-sm text-slate-700">{ref.locationText}</div>
                      {ref.valueText && <div className="mt-1 text-xs text-slate-500">命中值：{ref.valueText}</div>}
                    </div>
                    <div className="flex flex-col items-end gap-2 text-xs">
                      {refViewerHref && (
                        <a
                          href={refViewerHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => event.stopPropagation()}
                          className="text-emerald-700 hover:text-emerald-900"
                        >
                          高亮查看
                        </a>
                      )}
                      {refPageHref && (
                        <a
                          href={refPageHref}
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

                  {refPreviewUrl && (
                    <div className="mt-3 overflow-hidden rounded-lg border border-indigo-100 bg-slate-50">
                      {refViewerHref ? (
                        <a
                          href={refViewerHref}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => event.stopPropagation()}
                        >
                          <PreviewImage
                            src={refPreviewUrl}
                            alt={`${ref.role} 证据预览`}
                            maxHeightClass="max-h-[260px]"
                          />
                        </a>
                      ) : (
                        <PreviewImage
                          src={refPreviewUrl}
                          alt={`${ref.role} 证据预览`}
                          maxHeightClass="max-h-[260px]"
                        />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {previewUrl && (
        <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-rose-900">证据预览</div>
              <div className="mt-1 text-xs text-rose-700">
                {previewTarget?.bbox ? "已根据定位信息自动框选问题区域" : "当前页预览"}
              </div>
            </div>
            <div className="flex items-center gap-3 text-xs">
              {viewerHref && (
                <a
                  href={viewerHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(event) => event.stopPropagation()}
                  className="text-emerald-700 hover:text-emerald-900"
                >
                  全页高亮
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
                  打开原页
                </a>
              )}
            </div>
          </div>
          <div className="overflow-hidden rounded-lg border border-rose-100 bg-white">
            {viewerHref ? (
              <a
                href={viewerHref}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => event.stopPropagation()}
              >
                <PreviewImage src={previewUrl} alt={`${presentation.summary} 证据预览`} maxHeightClass="max-h-[360px]" />
              </a>
            ) : (
              <PreviewImage src={previewUrl} alt={`${presentation.summary} 证据预览`} maxHeightClass="max-h-[360px]" />
            )}
          </div>
        </div>
      )}

      {presentation.evidenceText && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="mb-2 text-sm font-medium text-slate-800">原文证据</div>
          <div className="rounded-lg bg-white px-3 py-2 text-sm leading-6 text-slate-700">
            {presentation.evidenceText}
          </div>
        </div>
      )}
    </div>
  );
}
