import type { CSSProperties } from "react";

import { type PageGeometry, toPreviewBbox } from "../../../lib/pageGeometry";

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
  geometry: PageGeometry | null = null,
): OverlayBox | null {
  return computeVerifiedOverlayBox(toPreviewBbox(problem.bbox, problem.page, geometry), naturalSize, PROBLEM_PREVIEW_SCALE);
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

/**
 * 标签必须贴在框外，不能压住框里的原文。
 *
 * 用标签自身高度做位移（translateY(-100%)）而不是固定 rem：窄面板里标签折行、
 * 字号变化时，固定偏移会让标签下半截落进框里，正好盖住被指出的金额。
 * 靠右的框改为右对齐，标签不伸出页面。
 */
export function getProblemOverlayLabelStyle(box: OverlayBox): CSSProperties {
  const vertical: CSSProperties =
    box.topPct > 8
      ? { top: `${box.topPct}%`, transform: "translateY(calc(-100% - 3px))" }
      : { top: `${box.topPct + box.heightPct}%`, transform: "translateY(3px)" };
  const right = box.leftPct + box.widthPct;
  if (box.leftPct > 55) {
    return { ...vertical, right: `${clamp(100 - right, 0, 100)}%`, maxWidth: `${clamp(right, 20, 100)}%` };
  }
  return { ...vertical, left: `${clamp(box.leftPct, 0, 100)}%`, maxWidth: `${clamp(100 - box.leftPct, 20, 100)}%` };
}
