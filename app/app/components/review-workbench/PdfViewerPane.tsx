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

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  getProblemOverlayLabelStyle,
  getProblemOverlayStyle,
} from "../task-review/problemPreview";
import { shouldDrawProblemOverlay } from "./reviewWorkbenchAdapters";
import type { Problem } from "../../../lib/mock";
import { computeOverlayBoxAtScale } from "./reviewWorkbenchAdapters";
import { toPreviewBbox, usePageGeometry } from "@/lib/pageGeometry";

const MIN_SCALE_PERCENT = 50;
const MAX_SCALE_PERCENT = 200;
const SCALE_STEP_PERCENT = 25;
const DEFAULT_SCALE_PERCENT = 100;
/** 中栏大图渲染缩放的换算系数：scale% / 100 * 该系数，得到真正传给后端的 scale
 *  查询参数。取 1.6（与旧模态证据大图的既有渲染倍率一致），保证 100% 缩放时
 *  中栏的清晰度与旧模态一致，用户往上调只会更清晰，不会突然变糊。 */
const RENDER_SCALE_BASE = 1.6;
/** 相邻页预取的内存上限（张）。预览接口是 no-store，不进浏览器缓存，预取结果只留在本组件。 */
const PREFETCH_CACHE_LIMIT = 6;

export interface PdfViewerPaneProps {
  jobId: string;
  totalPages: number | null;
  currentPage: number;
  onPageChange: (page: number) => void;
  /** 当前高亮的问题（选中问题时跳到对应页并高亮证据区域），未选中问题时为 null。 */
  highlightedProblem: Problem | null;
  leadingControls?: ReactNode;
}

function buildPageImageUrl(jobId: string, page: number, renderScale: number): string {
  const params = new URLSearchParams({ page: String(page), scale: String(renderScale), padding: "0" });
  return `/api/files/${encodeURIComponent(jobId)}/preview?${params.toString()}`;
}

export function PdfViewerPane({ jobId, totalPages, currentPage, onPageChange, highlightedProblem, leadingControls }: PdfViewerPaneProps) {
  const [scalePercent, setScalePercent] = useState(DEFAULT_SCALE_PERCENT);
  const [fitWidth, setFitWidth] = useState(true);
  const [loadedImage, setLoadedImage] = useState<{ url: string; width: number; height: number } | null>(null);
  const [imageLoadFailed, setImageLoadFailed] = useState(false);
  const [pageInput, setPageInput] = useState(String(currentPage));
  const [pageInputError, setPageInputError] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const pageGeometry = usePageGeometry(jobId);

  const renderScale = (scalePercent / 100) * RENDER_SCALE_BASE;
  const imageUrl = useMemo(
    () => buildPageImageUrl(jobId, currentPage, renderScale),
    [jobId, currentPage, renderScale],
  );

  // 切换页码或缩放后，之前那张图的自然尺寸不再对应新图，必须清空重新测量，
  // 否则高亮框会用旧图的尺寸换算新图的百分比，造成短暂的错位闪烁。
  useEffect(() => {
    setImageLoadFailed(false);
  }, [imageUrl]);
  useEffect(() => { setPageInput(String(currentPage)); setPageInputError(""); }, [currentPage]);
  const naturalSize = loadedImage?.url === imageUrl ? loadedImage : null;
  // 新页图片到达前显示占位，不留一片空白（服务端逐页渲染，繁忙时可能要等几秒）。
  const imageLoading = !imageLoadFailed && naturalSize === null;

  // 相邻页预取：当前页加载完成后再在后台取前后各一页，不与当前页抢后端渲染。
  // 取回的图片以 object URL 保存在内存里，翻到该页时直接显示；换文件/缩放或卸载时释放。
  const prefetchedRef = useRef(new Map<string, string>());
  useEffect(() => {
    const prefetched = prefetchedRef.current;
    return () => {
      prefetched.forEach((objectUrl) => URL.revokeObjectURL(objectUrl));
      prefetched.clear();
    };
  }, [jobId, renderScale]);
  useEffect(() => {
    if (!naturalSize || totalPages === null) return;
    const controller = new AbortController();
    const prefetched = prefetchedRef.current;
    const displayedUrl = buildPageImageUrl(jobId, currentPage, renderScale);
    for (const page of [currentPage + 1, currentPage - 1]) {
      if (page < 1 || page > totalPages) continue;
      const url = buildPageImageUrl(jobId, page, renderScale);
      if (prefetched.has(url)) continue;
      void fetch(url, { cache: "no-store", signal: controller.signal })
        .then((response) => (response.ok ? response.blob() : null))
        .then((blob) => {
          if (!blob || controller.signal.aborted || prefetched.has(url)) return;
          prefetched.set(url, URL.createObjectURL(blob));
          for (const [oldUrl, objectUrl] of Array.from(prefetched)) {
            if (prefetched.size <= PREFETCH_CACHE_LIMIT) break;
            if (oldUrl === url || oldUrl === displayedUrl) continue;
            URL.revokeObjectURL(objectUrl);
            prefetched.delete(oldUrl);
          }
        })
        .catch(() => {
          // 预取失败不影响翻页：翻到该页时照常按原地址加载并显示加载失败提示。
        });
    }
    return () => controller.abort();
  }, [jobId, currentPage, renderScale, totalPages, naturalSize]);
  const imageSrc = prefetchedRef.current.get(imageUrl) ?? imageUrl;

  // ZC-00（SYS-11）：高亮框必须属于当前页才绘制；无页码问题不绘制。
  const overlayProblem = shouldDrawProblemOverlay(highlightedProblem, currentPage)
    ? highlightedProblem
    : null;
  const overlayBox = overlayProblem
    ? computeOverlayBoxAtScale(toPreviewBbox(overlayProblem.bbox, currentPage, pageGeometry), naturalSize, renderScale)
    : null;
  const selectedSources = highlightedProblem?.findingSources
    ?? (highlightedProblem?.source === "ai" || highlightedProblem?.source === "rule"
      ? [highlightedProblem.source]
      : []);
  const isAiSelected = selectedSources.includes("ai");
  const isRuleSelected = selectedSources.includes("rule");
  const showUnlocatedAiNotice = Boolean(
    highlightedProblem
    && isAiSelected
    && shouldDrawProblemOverlay(highlightedProblem, currentPage)
    && !highlightedProblem.bbox,
  );

  const canGoPrev = currentPage > 1;
  const canGoNext = totalPages !== null && currentPage < totalPages;
  const selectedOtherPage = highlightedProblem?.page && highlightedProblem.page !== currentPage
    ? highlightedProblem.page : null;
  // 跳到新证据时将框滚动到可视区域；手动翻页则回到页首。
  const overlayTop = overlayBox?.topPct;
  const overlayLeft = overlayBox?.leftPct;
  useEffect(() => {
    const viewport = scrollRef.current;
    const img = imageRef.current;
    if (!viewport || !img || !naturalSize) return;
    const imageTop = img.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop;
    viewport.scrollTop = overlayTop === undefined
      ? 0 : Math.max(0, imageTop + img.clientHeight * overlayTop / 100 - viewport.clientHeight * 0.25);
    viewport.scrollLeft = 0;
  }, [imageUrl, naturalSize, highlightedProblem?.id, overlayTop, overlayLeft]);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="gbc-review-pdf-viewer">
      <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-0.5 border-b border-border px-2 py-1.5 text-xs">
        {leadingControls}
        <button
          type="button"
          onClick={() => onPageChange(currentPage - 1)}
          disabled={!canGoPrev}
          data-testid="gbc-review-pdf-prev-page"
          className="rounded-md px-1.5 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          上一页
        </button>
        <form className="flex items-center gap-1 text-slate-500" data-testid="gbc-review-pdf-current-page" onSubmit={(event) => {
          event.preventDefault();
          const value = Number(pageInput);
          if (!/^\d+$/.test(pageInput) || !Number.isSafeInteger(value) || value < 1 || (totalPages !== null && value > totalPages)) {
            setPageInputError(totalPages ? `请输入 1–${totalPages} 页` : "请输入有效页码");
            return;
          }
          setPageInputError(""); onPageChange(value);
        }}>
          <label htmlFor={`pdf-page-${jobId}`}>PDF 第</label>
          <input id={`pdf-page-${jobId}`} aria-label="PDF 页码" inputMode="numeric" value={pageInput}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => { setPageInput(event.target.value); setPageInputError(""); }}
            className="w-12 rounded border border-border bg-white px-1 py-1 text-center text-slate-700"
            data-testid="gbc-review-pdf-page-input" aria-invalid={Boolean(pageInputError)} />
          <span>页{totalPages ? ` / ${totalPages}` : ""}</span>
          <button type="submit" className="rounded px-1.5 py-1 text-primary-700 hover:bg-surface-100" data-testid="gbc-review-pdf-jump">跳转</button>
        </form>
        {pageInputError ? <span role="alert" className="text-warning-700">{pageInputError}</span> : null}
        <button
          type="button"
          onClick={() => onPageChange(currentPage + 1)}
          disabled={!canGoNext}
          data-testid="gbc-review-pdf-next-page"
          className="rounded-md px-1.5 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          下一页
        </button>
        <div className="mx-1 h-4 w-px bg-border" />
        <button
          type="button"
          onClick={() => {
            setFitWidth(false);
            setScalePercent((prev) => Math.max(MIN_SCALE_PERCENT, prev - SCALE_STEP_PERCENT));
          }}
          disabled={scalePercent <= MIN_SCALE_PERCENT}
          data-testid="gbc-review-pdf-zoom-out"
          className="rounded-md px-1.5 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          −
        </button>
        <span className="whitespace-nowrap text-center text-slate-500" data-testid="gbc-review-pdf-zoom-level">
          {scalePercent}%
        </span>
        <button
          type="button"
          onClick={() => {
            setFitWidth(false);
            setScalePercent((prev) => Math.min(MAX_SCALE_PERCENT, prev + SCALE_STEP_PERCENT));
          }}
          disabled={scalePercent >= MAX_SCALE_PERCENT}
          data-testid="gbc-review-pdf-zoom-in"
          className="rounded-md px-1.5 py-1 text-slate-600 transition-colors hover:bg-surface-100 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          +
        </button>
        <button
          type="button"
          onClick={() => { setFitWidth(true); setScalePercent(DEFAULT_SCALE_PERCENT); }}
          aria-pressed={fitWidth}
          data-testid="gbc-review-pdf-fit-width"
          className={`rounded-md px-1.5 py-1 transition-colors hover:bg-surface-100 ${fitWidth ? "text-primary-700" : "text-slate-500"}`}
        >适合宽度</button>
      </div>

      {!selectedOtherPage && highlightedProblem?.page && new Set(highlightedProblem.evidenceRefs?.map((ref) => ref.page)).size > 1 ? (
        <div className="shrink-0 border-b border-border px-3 py-1 text-xs text-slate-500">
          当前证据：PDF 第 {currentPage} 页。此问题含多页证据，可点击问题卡下方的引用切换。
        </div>
      ) : null}
      {selectedOtherPage ? (
        <div className="shrink-0 border-b border-border px-3 py-1 text-xs text-slate-500" data-testid="gbc-review-selected-page-mismatch">
          正在浏览 PDF 第 {currentPage} 页；所选证据在第 {selectedOtherPage} 页。
          <button type="button" className="ml-2 text-primary-700" onClick={() => onPageChange(selectedOtherPage)}>返回证据页</button>
        </div>
      ) : null}
      <div ref={scrollRef} className="min-h-0 min-w-0 flex-1 overflow-auto bg-surface-100 p-3">
        {showUnlocatedAiNotice ? (
          <div
            className="mx-auto mb-3 max-w-[900px] rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-xs leading-relaxed text-warning-800"
            data-testid="gbc-review-ai-location-unavailable"
          >
            大模型发现已跳转到第 {currentPage} 页，但引用没有通过原文定位，暂时无法画出问题框。请查看问题卡中的“未绘制定位框”原因。
          </div>
        ) : null}
        <div className="relative mx-auto" style={{ width: `${fitWidth ? 100 : scalePercent}%` }}>
          {imageLoadFailed ? (
            <div
              className="flex min-h-[400px] w-full items-center justify-center rounded-md border border-dashed border-border bg-white text-sm text-slate-400"
              data-testid="gbc-review-pdf-page-error"
            >
              页面加载失败
            </div>
          ) : (
            <>
            {imageLoading ? (
              <div
                className="flex aspect-[210/297] w-full animate-pulse items-center justify-center rounded-md border border-border bg-white text-sm text-slate-400"
                data-testid="gbc-review-pdf-page-loading"
                role="status"
              >
                正在加载第 {currentPage} 页…
              </div>
            ) : null}
            <img
              key={imageUrl}
              ref={imageRef}
              src={imageSrc}
              alt={`第 ${currentPage} 页`}
              data-testid="gbc-review-pdf-page-image"
              className={`${imageLoading ? "hidden" : "block"} h-auto w-full rounded-md border border-border bg-white shadow-sm`}
              onLoad={(event) => {
                const target = event.currentTarget;
                setLoadedImage({ url: imageUrl, width: target.naturalWidth, height: target.naturalHeight });
              }}
              onError={() => setImageLoadFailed(true)}
            />
            </>
          )}

          {overlayBox && overlayProblem ? (
            <>
              <div
                className="pointer-events-none absolute z-20"
                style={getProblemOverlayLabelStyle(overlayBox)}
                data-testid="gbc-review-evidence-label"
              >
                <div className={`inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 shadow-sm ${
                  isAiSelected
                    ? "border-info-600 bg-info-100/95 text-info-700"
                    : "border-warning-600 bg-warning-100/95 text-warning-700"
                }`}>
                  <span className="truncate">
                    {overlayProblem.source === "manual_correction"
                      ? `对照原页 · ${overlayProblem.title}`
                      : `${isAiSelected && isRuleSelected ? "共同发现原文" : isAiSelected ? "大模型引用原文" : "本地规则证据"} · ${overlayProblem.ruleId}`}
                  </span>
                </div>
              </div>
              <div
                className={`pointer-events-none absolute z-10 rounded-sm border-2 ${isAiSelected ? "border-info-700 bg-info-50/40" : "border-warning-700 bg-warning-50/50"}`}
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
