"use client";
import { useSearchParams } from "next/navigation";
import { ReviewListPage } from "./ReviewListPage";
import { ReviewWorkbenchPage } from "./ReviewWorkbenchPage";

export function ReviewEntryPage() {
  const job = useSearchParams().get("job");
  return job ? <ReviewWorkbenchPage key={job} /> : <ReviewListPage />;
}
