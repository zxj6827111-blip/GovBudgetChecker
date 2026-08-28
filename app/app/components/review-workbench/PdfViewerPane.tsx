/* eslint-disable @next/next/no-img-element */
/**
 * PdfViewerPane：审核工作台中栏 PDF 视图（Task 6.3）。
 *
 * 复用已有 bbox 定位逻辑：`computeOverlayBoxAtScale`（reviewWorkbenchAdapters.ts）
 * 复用 `task-review/problemPreview.ts` 的百分比换算公式（仅把硬编码的渲染缩放
 * 换成可变参数，理由见该文件顶部注释），`getProblemOverlayStyle`/
 * `getProblemOverlayLabelStyle` 直接从 problemPreview.ts 原样导入——这两个函数
 * 本身就与具体缩放值无关（吃的是已经算好的 OverlayBox），不存在"要不要重写"
 * 的问题。
 *
 * 页导航「上一页」「下一页」是必需的（对照任务书）：本领域"表格跨页断裂"是
 * 高频问题，只能看单页会漏判，因此本组件把上一页/下一页做成始终可见的常驻
 * 控件，不是需要额外点击展开的次要功能。
 */
"use client";

import { useEffect, useMemo, useState } from "react";

import {
  getProblemOverlayLabelStyle,
  getProblemOverlayStyle,
} from "../task-review/problemPreview";
import type { Problem } from "../../../lib/mock";
import { computeOverlayBoxAtScale } from "./reviewWorkbenchAdapters";

const MIN_SCALE_PERCENT = 50;
const MAX_SCALE_PERCENT = 200;
const SCALE_STEP_PERCENT = 25;
const DEFAULT_SCALE_PERCENT = 100;
/** 中栏大图渲染缩放的换算系数：scale% / 100 * 该系数，得到真正传给后端的 scale
 *  查询参数。取 1.6（与旧模态证据大图的既有渲染倍率一致），保证 100% 缩放时
 *  中栏的清晰度与旧模态一致，用户往上调只会更清晰，不会突然变糊。 */
const RENDER_SCALE_BASE = 1.6;

export interface PdfViewerPaneProps {
  jobId: string;
  totalPages: number | null;
  currentPage: number;
  onPageChange: (page: number) => void;
  /** 当前高亮的问题（选中问题时跳到对应页并高亮证据区域），未选中问题时为 null。 */
  highlightedProblem: Problem | null;
}

function buildPageImageUrl(jobId: string, page: number, renderScale: number): string {
  const params = new URLSearchParams({ page: String(page), scale: String(renderScale), padding: "0" });
  return `/api/files/${encodeURIComponent(jobId)}/preview?${params.toString()}`;
}

export function PdfViewerPane({ jobId, totalPages, currentPage, onPageChange, highlightedProblem }: PdfViewerPaneProps) {
  const [scalePercent, setScalePercent] = useState(DEFAULT_SCALE_PERCENT);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [imageLoadFailed, setImageLoadFailed] = useState(false);

  const renderScale = (scalePercent / 100) * RENDER_SCALE_BASE;
  const imageUrl = useMemo(
    () => buildPageImageUrl(jobId, currentPage, renderScale),
    [jobId, currentPage, renderScale],
  );

  // 切换页码或缩放后，之前那张图的自然尺寸不再对应新图，必须清空重新测量，
  // 否则高亮框会用旧图的尺寸换算新图的百分比，造成短暂的错位闪烁。
  useEffect(() => {
    setNaturalSize(null);
    setImageLoadFailed(false);
  }, [imageUrl]);

  const overlayBox = highlightedProblem
    ? computeOverlayBoxAtScale(highlightedProblem.bbox, naturalSize, renderScale)
    : null;

  const canGoPrev = currentPage > 1;
  const canGoNext = totalPages !== null && currentPage < totalPages;

  return (
    <div className="flex h-full flex-col" data-testid="gbc-review-pdf-viewer">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2 text-sm">
        <button
          type="button"
          onClick={() => onPageChange(currentPage - 1)}
          disabled={!canGoPrev}
          data-testid="gbc-review-pdf-prev-page"
          className="rounded-md px-2 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          上一页
        </button>
        <span className="text-slate-500" data-testid="gbc-review-pdf-current-page">
          第 {currentPage} 页
        </span>
        <button
          type="button"
          onClick={() => onPageChange(currentPage + 1)}
          disabled={!canGoNext}
          data-testid="gbc-review-pdf-next-page"
          className="rounded-md px-2 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          下一页
        </button>
        <div className="mx-1 h-4 w-px bg-border" />
        <button
          type="button"
          onClick={() => setScalePercent((prev) => Math.max(MIN_SCALE_PERCENT, prev - SCALE_STEP_PERCENT))}
          disabled={scalePercent <= MIN_SCALE_PERCENT}
          data-testid="gbc-review-pdf-zoom-out"
          className="rounded-md px-2 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          −
        </button>
        <span className="w-12 text-center text-slate-500" data-testid="gbc-review-pdf-zoom-level">
          缩放 {scalePercent}%
        </span>
        <button
          type="button"
          onClick={() => setScalePercent((prev) => Math.min(MAX_SCALE_PERCENT, prev + SCALE_STEP_PERCENT))}
          disabled={scalePercent >= MAX_SCALE_PERCENT}
          data-testid="gbc-review-pdf-zoom-in"
          className="rounded-md px-2 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          +
        </button>
      </div>

      <div className="flex-1 overflow-auto bg-surface-100 p-6">
        <div className="relative mx-auto inline-block">
          {imageLoadFailed ? (
            <div
              className="flex h-[600px] w-[440px] items-center justify-center rounded-md border border-dashed border-border bg-white text-sm text-slate-400"
              data-testid="gbc-review-pdf-page-error"
            >
              页面加载失败
            </div>
          ) : (
            <img
              key={imageUrl}
              src={imageUrl}
              alt={`第 ${currentPage} 页`}
              data-testid="gbc-review-pdf-page-image"
              className="block h-auto max-w-full rounded-md border border-border bg-white shadow-sm"
              onLoad={(event) => {
                const target = event.currentTarget;
                setNaturalSize({ width: target.naturalWidth, height: target.naturalHeight });
              }}
              onError={() => setImageLoadFailed(true)}
            />
          )}

          {overlayBox && highlightedProblem ? (
            <>
              <div
                className="pointer-events-none absolute z-20"
                style={getProblemOverlayLabelStyle(overlayBox)}
                data-testid="gbc-review-evidence-label"
              >
                <div className="inline-flex max-w-full items-center gap-1 rounded-md border border-warning-600 bg-warning-100 px-2 py-1 text-[11px] font-medium text-warning-700 shadow-sm">
                  问题证据 · {highlightedProblem.ruleId}
                </div>
              </div>
              <div
                className="pointer-events-none absolute z-10 rounded-sm border-2 border-warning-700 bg-warning-50/50"
                style={getProblemOverlayStyle(overlayBox)}
                data-testid="gbc-review-evidence-highlight"
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default PdfViewerPane;
