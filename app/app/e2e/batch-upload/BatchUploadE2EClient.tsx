"use client";

import { useState } from "react";

import BatchUploadModal from "../../components/BatchUploadModal";

export default function BatchUploadE2EClient() {
  const [completeCount, setCompleteCount] = useState(0);
  const [completedJobIds, setCompletedJobIds] = useState<string[]>([]);

  return (
    <main className="min-h-screen">
      <BatchUploadModal
        defaultDocType="dept_budget"
        onClose={() => {}}
        onComplete={(jobIds) => {
          setCompleteCount((v) => v + 1);
          setCompletedJobIds(jobIds);
        }}
      />
      <div data-testid="batch-complete-count" className="sr-only">
        {completeCount}
      </div>
      <div data-testid="batch-completed-job-ids" className="sr-only">
        {completedJobIds.join(",")}
      </div>
    </main>
  );
}
