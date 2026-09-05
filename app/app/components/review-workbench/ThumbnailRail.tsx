/**
 * ThumbnailRail：审核工作台左栏「页面缩略图」（Task 6.2，性能关键）。
 *
 * 复用已有接口 `GET /api/files/{job_id}/preview?page=N&scale=0.5`
 * （api/routes/files.py，支持任意页、bbox 裁剪、0.5-4.0 缩放），无需新增后端。
 *
 * 性能策略（对照任务书"⚠️ 性能硬要求"）：
 * 1. 懒加载：用 IntersectionObserver 监视每个缩略图占位元素，只有真正进入
 *    视口（含少量预取余量 rootMargin）才调用 scheduler.requestPage()；
 * 2. 并发上限：真正的调度逻辑在 ThumbnailLoadScheduler（reviewWorkbenchAdapters.ts）
 *    里，本组件只负责"什么时候该请求哪一页"，不重复发明并发控制；
 * 3. 客户端缓存：同一个 scheduler 实例在组件生命周期内持续存在，
 *    ThumbnailLoadScheduler 内部的 entries Map 就是缓存，翻页/滚动回来时
 *    已加载页不会重新请求。
 *
 * 缩略图用 scale=0.5（对照任务书示例 URL），远小于中栏大图的 scale，
 * 是缩略图场景下合理的性能取舍——分辨率足够辨认页面轮廓与文字密度，
 * 但传输体积远小于全尺寸渲染。
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ThumbnailLoadScheduler, type ThumbnailEntry } from "./reviewWorkbenchAdapters";

const THUMBNAIL_MAX_CONCURRENT_REQUESTS = 4;
const THUMBNAIL_SCALE = 0.5;
/** 提前多少像素开始加载：让缩略图在滚入视口前已经开始请求，减少可感知的空白闪烁。 */
const PREFETCH_ROOT_MARGIN = "200px 0px";

export interface ThumbnailRailProps {
  jobId: string;
  totalPages: number | null;
  currentPage: number;
  onSelectPage: (page: number) => void;
}

function buildThumbnailUrl(jobId: string, page: number): string {
  const params = new URLSearchParams({ page: String(page), scale: String(THUMBNAIL_SCALE) });
  return `/api/files/${encodeURIComponent(jobId)}/preview?${params.toString()}`;
}

/** 单个缩略图格子：自己负责 IntersectionObserver 订阅与 scheduler 状态读取，
 *  避免父组件为 48 个格子都手写重复的可见性判断逻辑。 */
function ThumbnailCell({
  page,
  jobId,
  isActive,
  scheduler,
  onSelect,
}: {
  page: number;
  jobId: string;
  isActive: boolean;
  scheduler: ThumbnailLoadScheduler;
  onSelect: (page: number) => void;
}) {
  const cellRef = useRef<HTMLButtonElement>(null);
  const [entry, setEntry] = useState<ThumbnailEntry>(() => scheduler.getEntry(page));

  useEffect(() => {
    const unsubscribe = scheduler.subscribe(() => setEntry(scheduler.getEntry(page)));
    return unsubscribe;
  }, [page, scheduler]);

  useEffect(() => {
    const node = cellRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      // 测试环境或极旧浏览器没有 IntersectionObserver 时，退化为"直接请求"
      // （懒加载是性能优化，不是功能前提，缺失时不应该整个缩略图栏空白）。
      scheduler.requestPage(page);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const observerEntry of entries) {
          if (observerEntry.isIntersecting) {
            scheduler.requestPage(page);
          }
        }
      },
      { rootMargin: PREFETCH_ROOT_MARGIN },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [page, scheduler]);

  return (
    <button
      ref={cellRef}
      type="button"
      onClick={() => onSelect(page)}
      data-testid={`gbc-review-thumbnail-${page}`}
      data-status={entry.status}
      aria-current={isActive || undefined}
      className={`flex aspect-[3/4] w-full flex-col items-center justify-center overflow-hidden rounded-md border-2 bg-white text-xs transition-colors ${
        isActive ? "border-primary-600 shadow-sm" : "border-border hover:border-primary-300"
      }`}
    >
      {entry.status === "loaded" && entry.src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={entry.src} alt={`第 ${page} 页缩略图`} className="h-full w-full object-contain" />
      ) : entry.status === "error" ? (
        <span className="text-slate-400">加载失败</span>
      ) : (
        <span className="text-slate-300">{entry.status === "loading" ? "加载中…" : page}</span>
      )}
      <span className={`mt-1 shrink-0 text-[11px] ${isActive ? "font-semibold text-primary-700" : "text-slate-400"}`}>
        {page}
      </span>
    </button>
  );
}

export function ThumbnailRail({ jobId, totalPages, currentPage, onSelectPage }: ThumbnailRailProps) {
  // scheduler 必须在 jobId 变化时重建（切换任务后缓存不应该跨任务复用），
  // 否则会把上一个任务的缩略图缓存错误地当成当前任务的结果展示。
  const scheduler = useMemo(
    () =>
      new ThumbnailLoadScheduler({
        maxConcurrent: THUMBNAIL_MAX_CONCURRENT_REQUESTS,
        fetchPage: async (page) => {
          const response = await fetch(buildThumbnailUrl(jobId, page), { cache: "no-store" });
          if (!response.ok) {
            throw new Error(`preview request failed: ${response.status}`);
          }
          const blob = await response.blob();
          return URL.createObjectURL(blob);
        },
      }),
    [jobId],
  );

  useEffect(() => {
    // 组件卸载/任务切换时释放所有已创建的 object URL，避免内存泄漏。
    return () => {
      if (!totalPages) {
        return;
      }
      for (let page = 1; page <= totalPages; page += 1) {
        const entry = scheduler.getEntry(page);
        if (entry.src) {
          URL.revokeObjectURL(entry.src);
        }
      }
    };
  }, [scheduler, totalPages]);

  if (totalPages === null) {
    return (
      <div className="p-4 text-center text-xs text-slate-400" data-testid="gbc-review-thumbnail-rail-loading">
        正在加载页面缩略图…
      </div>
    );
  }

  const pages = Array.from({ length: totalPages }, (_, index) => index + 1);

  return (
    <div className="flex h-full flex-col" data-testid="gbc-review-thumbnail-rail">
      <div className="shrink-0 border-b border-border px-3 py-2 text-xs font-medium text-slate-500">
        页面缩略图 <span className="font-semibold text-slate-700">{currentPage}</span> / {totalPages}
      </div>
      <div className="grid flex-1 grid-cols-2 gap-2 overflow-y-auto p-3">
        {pages.map((page) => (
          <ThumbnailCell
            key={page}
            page={page}
            jobId={jobId}
            isActive={page === currentPage}
            scheduler={scheduler}
            onSelect={onSelectPage}
          />
        ))}
      </div>
    </div>
  );
}

export default ThumbnailRail;
