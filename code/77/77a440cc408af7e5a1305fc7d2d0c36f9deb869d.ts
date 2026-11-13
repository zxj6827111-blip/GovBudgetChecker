import type { CSSProperties } from "react";

import type { Problem } from "@/lib/mock";

export const PROBLEM_PREVIEW_SCALE = 1.6;

type NaturalSize = {
  width: number;
  height: number;
};

type OverlayBox = {
  leftPct: number;
  topPct: number;
  widthPct: number;
  heightPct: number;
};

export type { OverlayBox };

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function normalizeProblemBbox(raw: unknown): number[] | undefined {
  if (!Array.isArray(raw) || raw.length !== 4) {
    return undefined;
  }

  const values = raw.map((item) => Number(item));
  if (values.some((item) => !Number.isFinite(item))) {
    return undefined;
  }
  if (values[2] <= values[0] || values[3] <= values[1]) {
    return undefined;
  }

  return values;
}

export function buildProblemPreviewUrl(jobId?: string, page?: number | null): string | null {
  if (!jobId || !page) {
    return null;
  }

  const params = new URLSearchParams({
    page: String(page),
    scale: String(PROBLEM_PREVIEW_SCALE),
    padding: "0",
  });

  return `/api/files/${encodeURIComponent(jobId)}/preview?${params.toString()}`;
}

export function buildProblemPdfPageUrl(jobId?: string, page?: number | null): string | null {
  if (!jobId || !page) {
    return null;
  }

  return `/api/files/${encodeURIComponent(jobId)}/source#page=${page}`;
}

export function getProblemPreviewSource(problem: Problem): string {
  return buildProblemPreviewUrl(problem.jobId, problem.page) ?? problem.evidenceImage;
}

export function getProblemOverlayLabel(problem: Problem, maxLength = 42): string {
  const source = problem.title || problem.description || problem.location || problem.ruleId;
  const text = source.trim();
  if (text.length <= maxLength) {
    return text;
  }

  return `${text.slice(0, Math.max(1, maxLength - 1)).trim()}...`;
}

export function getProblemOverlayBox(
  problem: Problem,
  naturalSize: NaturalSize | null,
): OverlayBox | null {
  return computeVerifiedOverlayBox(problem.bbox, naturalSize, PROBLEM_PREVIEW_SCALE);
}

export function computeVerifiedOverlayBox(raw: unknown, naturalSize: NaturalSize | null, scale: number): OverlayBox | null {
  const bbox = normalizeProblemBbox(raw);
  if (!bbox || !naturalSize || naturalSize.width <= 0 || naturalSize.height <= 0 || scale <= 0) return null;
  // 图片像素向上取整最多偏离一个像素；明显越界说明坐标不属于此页面，禁止绘制。
  if (bbox[0] < 0 || bbox[1] < 0 || bbox[2] * scale > naturalSize.width + 1 || bbox[3] * scale > naturalSize.height + 1) return null;
  return {
    leftPct: bbox[0] * scale / naturalSize.width * 100,
    topPct: bbox[1] * scale / naturalSize.height * 100,
    widthPct: (bbox[2] - bbox[0]) * scale / naturalSize.width * 100,
    heightPct: (bbox[3] - bbox[1]) * scale / naturalSize.height * 100,
  };
}

export function getProblemOverlayStyle(box: OverlayBox): CSSProperties {
  return {
    left: `${box.leftPct}%`,
    top: `${box.topPct}%`,
    width: `${box.widthPct}%`,
    height: `${box.heightPct}%`,
  };
}

export function getProblemOverlayLabelStyle(box: OverlayBox): CSSProperties {
  const labelWidth = clamp(Math.max(box.widthPct + 8, 24), 24, 56);
  const labelLeft = clamp(box.leftPct, 1.5, 100 - labelWidth - 1.5);
  const labelTop =
    box.topPct > 12
      ? `calc(${box.topPct}% - 2.3rem)`
      : `calc(${box.topPct + box.heightPct}% + 0.35rem)`;

  return {
    left: `${labelLeft}%`,
    top: labelTop,
    maxWidth: `${labelWidth}%`,
  };
}
