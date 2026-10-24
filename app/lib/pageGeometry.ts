import { useEffect, useState } from "react";

/** 页码 → 解析坐标到预览图坐标的仿射 [a, b, c, d, e, f]；只含两者不一致的页。 */
export type PageGeometry = Record<string, number[]>;

/**
 * 证据框按解析坐标（pdfplumber 的 MediaBox 顶部坐标）存储，预览图按可见页渲染。
 * MediaBox 原点不是 (0, 0) 的 PDF 两者相差一个平移，画框前按四角换算。
 */
export function toPreviewBbox(
  bbox: number[] | undefined,
  page: number | null | undefined,
  geometry: PageGeometry | null,
): number[] | undefined {
  if (!bbox || bbox.length !== 4 || !page) return bbox;
  const matrix = geometry?.[String(page)];
  if (!matrix || matrix.length !== 6 || matrix.some((value) => !Number.isFinite(value))) return bbox;
  const [a, b, c, d, e, f] = matrix;
  const xs: number[] = [];
  const ys: number[] = [];
  for (const x of [bbox[0], bbox[2]]) {
    for (const y of [bbox[1], bbox[3]]) {
      xs.push(a * x + c * y + e);
      ys.push(b * x + d * y + f);
    }
  }
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

const requests = new Map<string, Promise<PageGeometry>>();

function loadPageGeometry(jobId: string): Promise<PageGeometry> {
  let request = requests.get(jobId);
  if (!request) {
    request = fetch(`/api/files/${encodeURIComponent(jobId)}/page-geometry`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((payload) => (payload?.native_to_preview ?? {}) as PageGeometry);
    // 取失败不缓存：下次仍可重试，这期间按原坐标画框（与没有换算时一致）。
    request.catch(() => requests.delete(jobId));
    requests.set(jobId, request);
  }
  return request;
}

export function usePageGeometry(jobId?: string | null): PageGeometry | null {
  const [geometry, setGeometry] = useState<PageGeometry | null>(null);
  useEffect(() => {
    if (!jobId) return undefined;
    let live = true;
    loadPageGeometry(jobId).then((value) => live && setGeometry(value), () => undefined);
    return () => {
      live = false;
    };
  }, [jobId]);
  return geometry;
}
