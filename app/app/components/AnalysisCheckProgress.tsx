type Track = { state?: string; completed?: number; total?: number | null; percent?: number | null; current_step?: string; model?: string; failed?: number; unit?: string };
const labels: Record<string, string> = { waiting: "等待解析", queued: "排队中", running: "检查中", completed: "已完成", degraded: "部分完成", failed: "失败", skipped: "未启用", cancelled: "已取消" };

export default function AnalysisCheckProgress({ value, compact = false }: { value: unknown; compact?: boolean }) {
  if (!value || typeof value !== "object") return null;
  const tracks = value as { local?: Track; ai?: Track };
  if (!tracks.local && !tracks.ai) return null;
  return <div className={`${compact ? "mt-2 space-y-2" : "grid gap-3 rounded-md border border-slate-200 bg-white p-3 sm:grid-cols-2"}`} data-testid="analysis-check-progress" aria-live="polite">
    {(["local", "ai"] as const).map(key => {
      const p = tracks[key]; if (!p) return null;
      const failed = p.state === "failed" || p.state === "degraded";
      const known = typeof p.percent === "number" && Number.isFinite(p.percent);
      const percent = known ? Math.min(100, Math.max(0, p.percent!)) : undefined;
      return <div key={key} data-testid={`check-progress-${key}`} className="min-w-0 text-xs">
        <div className="flex justify-between gap-2"><strong>{key === "local" ? "本地检查" : "大模型检查"}</strong><span className={failed ? "text-amber-700" : "text-slate-600"}>{labels[p.state || ""] || "状态未知"}{known ? ` · ${percent}%` : ""}</span></div>
        <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-100" role="progressbar" aria-label={key === "local" ? "本地检查进度" : "大模型检查进度"} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-valuetext={known ? `${percent}%` : labels[p.state || ""] || "进度未知"}>
          {known && <div className={`h-full ${failed ? "bg-amber-500" : key === "ai" ? "bg-violet-500" : "bg-blue-500"}`} style={{ width: `${percent}%` }} />}
        </div>
        {p.model && <div className="mt-1 break-all text-slate-500">模型：{p.model}</div>}
        <div className="mt-1 text-slate-500">{p.total != null && p.total > 0 ? `${p.completed || 0}/${p.total} ${p.unit || "项"} · ` : ""}{p.current_step}{p.failed ? `（${p.failed} 项失败）` : ""}</div>
      </div>;
    })}
  </div>;
}
