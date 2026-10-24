import { Suspense } from "react";

import { ReviewEntryPage } from "@/components/review-workbench/ReviewEntryPage";

export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <span className="text-sm text-slate-500">正在加载…</span>
        </div>
      }
    >
      <ReviewEntryPage />
    </Suspense>
  );
}
