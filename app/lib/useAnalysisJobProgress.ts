"use client";
import { useEffect, useState } from "react";

type LiveJob = { status?: string; stage?: string; check_progress?: unknown; unavailable?: boolean };
export function useAnalysisJobProgress(jobIds: string[]) {
  const [jobs, setJobs] = useState<Record<string, LiveJob>>({});
  const key = jobIds.join(",");
  useEffect(() => {
    const pending = new Set(key.split(",").filter(Boolean));
    if (!pending.size) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      await Promise.allSettled(Array.from(pending).map(async id => {
        try {
          const res = await fetch(`/api/jobs/${encodeURIComponent(id)}/status`, { cache: "no-store", signal: controller.signal });
          if (!res.ok) throw new Error();
          const data = await res.json();
          if (cancelled) return;
          setJobs(old => ({ ...old, [id]: { status: data.status, stage: data.stage, check_progress: data.check_progress } }));
          if (["done", "review_required", "degraded", "error", "cancelled"].includes(data.status)) pending.delete(id);
        } catch { if (!cancelled) setJobs(old => ({ ...old, [id]: { ...old[id], unavailable: true } })); }
      }));
      if (!cancelled && pending.size) timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [key]);
  return jobs;
}
