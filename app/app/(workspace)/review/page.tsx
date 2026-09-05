import { Suspense } from "react";

import { ReviewWorkbenchPage } from "@/components/review-workbench/ReviewWorkbenchPage";

export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <span className="text-sm text-slate-500">正在加载…</span>
        </div>
      }
    >
      <ReviewWorkbenchPage />
    </Suspense>
  );
}
