/* eslint-disable @next/next/no-img-element */
"use client";

import { Maximize2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { Problem } from "@/lib/mock";
import { cn } from "@/lib/utils";

import {
  getProblemOverlayBox,
  getProblemOverlayLabel,
  getProblemOverlayLabelStyle,
  getProblemOverlayStyle,
  getProblemPreviewSource,
} from "./problemPreview";

type ProblemPreviewFrameProps = {
  problem: Problem;
  onClick?: () => void;
  frameClassName?: string;
  canvasClassName?: string;
  imageClassName?: string;
  overlayClassName?: string;
  labelClassName?: string;
  showHoverHint?: boolean;
  hoverHintText?: string;
};

export default function ProblemPreviewFrame({
  problem,
  onClick,
  frameClassName,
  canvasClassName,
  imageClassName,
  overlayClassName,
  labelClassName,
  showHoverHint = false,
  hoverHintText = "点击查看大图",
}: ProblemPreviewFrameProps) {
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const previewSource = useMemo(() => getProblemPreviewSource(problem), [problem]);
  const overlayBox = useMemo(
    () => getProblemOverlayBox(problem, naturalSize),
    [naturalSize, problem],
  );
  const overlayLabel = useMemo(() => getProblemOverlayLabel(problem), [problem]);

  return (
    <div
      className={cn(
        "relative overflow-auto",
        onClick && "group cursor-zoom-in",
        frameClassName,
      )}
      onClick={onClick}
    >
      <div className={cn("flex items-start justify-center p-4", canvasClassName)}>
        <div className="relative inline-block">
          <img
            src={previewSource}
            alt={`${problem.title} 证据预览`}
            className={cn("block h-auto w-auto max-w-full bg-white", imageClassName)}
            referrerPolicy="no-referrer"
            onLoad={(event) => {
              const target = event.currentTarget;
              setNaturalSize({
                width: target.naturalWidth,
                height: target.naturalHeight,
              });
            }}
          />

          {overlayBox && (
            <>
              <div
                className="pointer-events-none absolute z-20"
                style={getProblemOverlayLabelStyle(overlayBox)}
              >
                <div
                  className={cn(
                    "inline-flex max-w-full rounded-full border border-rose-200 bg-white/96 px-3 py-1.5 text-xs font-medium text-rose-700 shadow-lg backdrop-blur",
                    labelClassName,
                  )}
                >
                  <span className="truncate">{overlayLabel}</span>
                </div>
              </div>

              <div
                className={cn(
                  "pointer-events-none absolute z-10 rounded-md border-[3px] border-rose-500 bg-rose-500/10",
                  overlayClassName,
                )}
                style={getProblemOverlayStyle(overlayBox)}
              />
            </>
          )}
        </div>
      </div>

      {showHoverHint && onClick && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
          <div className="flex items-center gap-2 rounded-lg bg-slate-950/82 px-4 py-2 text-sm font-medium text-white shadow-xl backdrop-blur-sm">
            <Maximize2 className="h-4 w-4" />
            {hoverHintText}
          </div>
        </div>
      )}
    </div>
  );
}
