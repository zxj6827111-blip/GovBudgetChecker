"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { REVIEW_WORKLIST_INVALIDATED } from "@/lib/reviewWorklistEvents";
import { reviewWorklistRequest } from "@/lib/listRequests";
import { nextReviewItem, reviewDetailPath, type ReviewListItem } from "./reviewListAdapters";

export function ReviewDetailNavigation({ jobId, returnTo }: { jobId: string; returnTo: string }) {
  const [next, setNext] = useState<ReviewListItem | null>(null);
  useEffect(() => {
    if (!returnTo.startsWith("/review")) { setNext(null); return; }
    let cancelled = false;
    let version = 0;
    async function load() {
      const requestVersion = ++version;
      try {
        const data = await reviewWorklistRequest.load();
        if (!cancelled && requestVersion === version) setNext(nextReviewItem(data.items, jobId, returnTo));
      } catch { if (!cancelled && requestVersion === version) setNext(null); }
    }
    void load();
    const refresh = () => void load();
    window.addEventListener(REVIEW_WORKLIST_INVALIDATED, refresh);
    return () => { cancelled = true; window.removeEventListener(REVIEW_WORKLIST_INVALIDATED, refresh); };
  }, [jobId, returnTo]);
  return next?.job_id ? <Link href={reviewDetailPath(next.job_id, returnTo) as Route}
    data-testid="gbc-review-next" className="whitespace-nowrap rounded-md border border-border px-3 py-1.5 text-xs text-primary-700 hover:bg-primary-50">查看下一份</Link> : null;
}
