"use client";

import { useState } from "react";

import BatchUploadModal from "../../components/BatchUploadModal";

export default function BatchUploadE2EPage() {
  const [completeCount, setCompleteCount] = useState(0);

  return (
    <main className="min-h-screen">
      <BatchUploadModal
        defaultDocType="dept_budget"
        onClose={() => {}}
        onComplete={() => setCompleteCount((v) => v + 1)}
      />
      <div data-testid="batch-complete-count" className="sr-only">
        {completeCount}
      </div>
    </main>
  );
}
