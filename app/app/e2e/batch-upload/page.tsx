import { notFound } from "next/navigation";

import BatchUploadE2EClient from "./BatchUploadE2EClient";

export default function BatchUploadE2EPage() {
  if (
    process.env.NODE_ENV === "production" &&
    process.env.GBC_ENABLE_E2E_PAGES !== "true"
  ) {
    notFound();
  }

  return <BatchUploadE2EClient />;
}
