"use client";

import { useEffect, useRef, useState } from "react";
import type { CockpitProgress, CockpitTrend } from "./workbenchCockpitAdapters";
import { cockpitPercent } from "./workbenchCockpitAdapters";

export function CockpitDonut({ progress }: { progress: CockpitProgress | null }) {
  const circumference = 2 * Math.PI * 47;
  const segments = progress ? [
    { value: progress.completed ?? 0, color: "stroke-success-600" },
    { value: progress.review, color: "stroke-warning-600" },
    { value: progress.processing, color: "stroke-info-500" },
    { value: progress.failed, color: "stroke-danger-600" },
    { value: progress.other, color: "stroke-slate-300" },
  ] : [];
  let offset = 0;
  return (
    <svg className="h-28 w-28 shrink-0" viewBox="0 0 128 128" role="img"
      aria-label={progress ? `共${progress.total}条，待复核${progress.review}条，已复核${progress.completed ?? "未知"}条` : "审校统计加载中"}>
      <circle cx="64" cy="64" r="47" fill="none" className="stroke-surface-100" strokeWidth="10" />
      {segments.map((segment, index) => {
        const length = progress?.total ? segment.value / progress.total * circumference : 0;
        const start = offset; offset += length;
        return length > 0 ? <circle key={index} cx="64" cy="64" r="47" fill="none" className={segment.color}
          strokeWidth="10" strokeDasharray={`${length} ${circumference - length}`} strokeDashoffset={-start}
          transform="rotate(-90 64 64)" /> : null;
      })}
      <text x="64" y="62" textAnchor="middle" className="fill-slate-900 text-[23px] font-semibold">
        {progress?.completionRatio != null ? cockpitPercent(progress.completionRatio) : progress?.total === 0 ? "0" : "—"}
      </text>
      <text x="64" y="81" textAnchor="middle" className="fill-slate-400 text-[11px]">
        {progress?.total === 0 ? "暂无材料" : progress?.completed == null ? "暂无复核统计" : "已复核占比"}
      </text>
    </svg>
  );
}

export function CockpitTrendChart({ trend }: { trend: CockpitTrend | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(400);
  const [hovered, setHovered] = useState<number | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(160, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  if (!trend) return <div ref={ref} className="flex h-32 items-center justify-center text-sm text-slate-400">正在加载趋势…</div>;
  const { points } = trend;
  const height = 128, left = 35, right = 16, top = 22, bottom = 29;
  const ceiling = Math.max(2, Math.ceil(Math.max(...points.flatMap((point) => [point.uploaded, point.analyzed])) / 2) * 2);
  const x = (index: number) => left + index / (points.length - 1) * (width - left - right);
  const y = (value: number) => top + (height - top - bottom) * (1 - value / ceiling);
  const hoverIndex = hovered === null ? null : Math.min(hovered, points.length - 1);
  const point = hoverIndex === null ? null : points[hoverIndex];
  const tickStep = points.length > 7 ? Math.ceil((points.length - 1) / (width < 400 ? 3 : 5)) : width < 380 ? 3 : 1;
  return (
    <div ref={ref} className="relative min-w-0" data-testid="gbc-cockpit-trend-chart">
      <svg className="block h-auto w-full" viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`新增任务${trend.uploadedTotal}个，结束分析${trend.analyzedTotal}个；分析结束不代表人工复核完成`}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const relative = (event.clientX - bounds.left) / bounds.width * width;
          setHovered(Math.max(0, Math.min(points.length - 1, Math.round((relative - left) / (width - left - right) * (points.length - 1)))));
        }} onPointerLeave={() => setHovered(null)}>
        <text x={left} y="12" className="fill-slate-400 text-[11px]">任务数</text>
        {[0, ceiling / 2, ceiling].map((value) => <g key={value}>
          <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="stroke-slate-100" />
          <text x={left - 8} y={y(value) + 4} textAnchor="end" className="fill-slate-400 text-[11px]">{value}</text>
        </g>)}
        {(["uploaded", "analyzed"] as const).map((series) => <g key={series}>
          <polyline points={points.map((value, index) => `${x(index)},${y(value[series])}`).join(" ")}
            fill="none" strokeWidth="2" className={series === "uploaded" ? "stroke-info-500" : "stroke-primary-600"} />
          {points.map((value, index) => <circle key={value.date} cx={x(index)} cy={y(value[series])}
            r={points.length > 7 ? 1.5 : 2.7} className={series === "uploaded" ? "fill-info-500" : "fill-primary-600"} />)}
        </g>)}
        {points.map((value, index) => (index % tickStep === 0 || index === points.length - 1)
          ? <text key={value.date} x={x(index)} y={height - 7} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}
            className="fill-slate-400 text-[11px]">{value.label}</text> : null)}
        {hoverIndex !== null ? <line x1={x(hoverIndex)} x2={x(hoverIndex)} y1={top} y2={height - bottom} className="stroke-slate-300" strokeDasharray="3 3" /> : null}
      </svg>
      {point ? <div className="pointer-events-none absolute right-2 top-1 rounded-md border border-border bg-white px-2 py-1 text-xs text-slate-600 shadow-sm">
        {point.label} · 新增 {point.uploaded} · 结束分析 {point.analyzed}
      </div> : null}
      <details className="mt-1 text-xs text-slate-400"><summary className="cursor-pointer" data-testid="gbc-cockpit-trend-summary">
        近{points.length}日新增 {trend.uploadedTotal} 个任务 · {trend.analyzedTotal} 个结束分析<span className="ml-2 text-primary-700">查看每日统计</span>
      </summary>
        <div className="mt-2 grid grid-cols-2 gap-1 sm:grid-cols-3">{points.map((value) => <span key={value.date}>{value.label}：新增 {value.uploaded} / 分析结束 {value.analyzed}</span>)}</div>
      </details>
    </div>
  );
}
