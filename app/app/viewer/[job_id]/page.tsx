"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

type ViewerPageProps = {
  params: {
    job_id: string;
  };
};

const PREVIEW_SCALE = 1.6;

function toPositivePage(value: string | null): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return 1;
  return Math.floor(parsed);
}

function parseBbox(raw: string | null): number[] | null {
  if (!raw) return null;
  const values = raw.split(",").map((item) => Number(item.trim()));
  if (values.length !== 4 || values.some((item) => !Number.isFinite(item))) return null;
  if (values[2] <= values[0] || values[3] <= values[1]) return null;
  return values;
}

export default function ViewerPage({ params }: ViewerPageProps) {
  const searchParams = useSearchParams();
  const page = toPositivePage(searchParams.get("page"));
  const bbox = parseBbox(searchParams.get("bbox"));
  const title = searchParams.get("title") || "问题高亮查看";
  const location = searchParams.get("location") || "";
  const [zoom, setZoom] = useState(100);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);

  const previewUrl = useMemo(() => {
    const paramsObj = new URLSearchParams({
      page: String(page),
      scale: String(PREVIEW_SCALE),
      padding: "0",
    });
    return `/api/files/${params.job_id}/preview?${paramsObj.toString()}`;
  }, [page, params.job_id]);

  const sourceUrl = `/api/files/${params.job_id}/source#page=${page}`;

  const overlayStyle = useMemo(() => {
    if (!bbox || !naturalSize) return null;
    return {
      left: `${(bbox[0] * PREVIEW_SCALE / naturalSize.width) * 100}%`,
      top: `${(bbox[1] * PREVIEW_SCALE / naturalSize.height) * 100}%`,
      width: `${((bbox[2] - bbox[0]) * PREVIEW_SCALE / naturalSize.width) * 100}%`,
      height: `${((bbox[3] - bbox[1]) * PREVIEW_SCALE / naturalSize.height) * 100}%`,
    };
  }, [bbox, naturalSize]);

  const prevHref = `/viewer/${params.job_id}?page=${Math.max(1, page - 1)}`;
  const nextHref = `/viewer/${params.job_id}?page=${page + 1}`;

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#f5f7fb,_#e7edf6_45%,_#d8e1ed)] text-slate-900">
      <div className="mx-auto max-w-[1600px] px-4 py-6 md:px-8">
        <div className="mb-6 rounded-3xl border border-white/70 bg-white/80 shadow-[0_20px_60px_rgba(15,23,42,0.10)] backdrop-blur">
          <div className="flex flex-col gap-4 border-b border-slate-200/80 px-5 py-5 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">Highlight Viewer</div>
              <h1 className="mt-2 text-xl font-semibold leading-snug text-slate-900 md:text-2xl">{title}</h1>
              {location && <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{location}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-800">
                第 {page} 页
              </span>
              <a
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-100"
              >
                打开原 PDF ↗
              </a>
            </div>
          </div>

          <div className="grid gap-6 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_280px]">
            <section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="text-sm text-slate-600">
                  {bbox ? "红框位置基于结构化 bbox 自动定位，可用于快速复核问题区域。" : "当前问题暂无 bbox，显示整页预览。"}
                </div>
                <div className="flex items-center gap-2">
                  <a
                    href={prevHref}
                    className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-sm font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-100"
                  >
                    上一页
                  </a>
                  <a
                    href={nextHref}
                    className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1 text-sm font-medium text-slate-700 hover:border-slate-300 hover:bg-slate-100"
                  >
                    下一页
                  </a>
                </div>
              </div>

              <div className="overflow-auto rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mx-auto" style={{ width: `${zoom}%`, maxWidth: "1400px" }}>
                  <div className="relative inline-block w-full">
                    <Image
                      src={previewUrl}
                      alt={`第 ${page} 页预览`}
                      width={1800}
                      height={2400}
                      unoptimized
                      className="block h-auto w-full rounded-xl border border-slate-200 bg-white shadow-sm"
                      onLoad={(event) => {
                        const target = event.currentTarget as HTMLImageElement;
                        setNaturalSize({
                          width: target.naturalWidth,
                          height: target.naturalHeight,
                        });
                      }}
                    />
                    {overlayStyle && (
                      <div
                        className="pointer-events-none absolute rounded-md border-[3px] border-rose-500 bg-rose-500/10 shadow-[0_0_0_6px_rgba(244,63,94,0.10)]"
                        style={overlayStyle}
                      />
                    )}
                  </div>
                </div>
              </div>
            </section>

            <aside className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">Viewer Controls</div>
              <div className="mt-4 space-y-5">
                <div>
                  <div className="mb-2 text-sm font-medium text-slate-800">缩放</div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setZoom((value) => Math.max(60, value - 10))}
                      className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300 hover:bg-slate-100"
                    >
                      -
                    </button>
                    <div className="min-w-[72px] text-center text-sm font-medium text-slate-700">{zoom}%</div>
                    <button
                      type="button"
                      onClick={() => setZoom((value) => Math.min(180, value + 10))}
                      className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300 hover:bg-slate-100"
                    >
                      +
                    </button>
                  </div>
                </div>

                <div>
                  <div className="mb-2 text-sm font-medium text-slate-800">当前定位</div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-600">
                    <div>页码：第 {page} 页</div>
                    <div>bbox：{bbox ? bbox.map((value) => value.toFixed(2)).join(", ") : "无"}</div>
                  </div>
                </div>

                <div>
                  <div className="mb-2 text-sm font-medium text-slate-800">说明</div>
                  <p className="text-sm leading-6 text-slate-600">
                    这个页面使用后端预览接口渲染整页，再按问题 bbox 叠加红框，适合快速确认问题是否落在正确的单元格、行或说明段落附近。
                  </p>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </div>
    </main>
  );
}


