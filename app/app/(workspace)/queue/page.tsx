import { Suspense } from "react";

import { QueuePage } from "@/components/queue/QueuePage";

export default function QueueRoutePage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <span className="text-sm text-slate-500">正在加载…</span>
        </div>
      }
    >
      <QueuePage />
    </Suspense>
  );
}
