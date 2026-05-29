import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalJobDetail, getLocalStructuredIngest, LocalDataError } from "@/lib/localData";

export const dynamic = "force-dynamic";

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ jobUuid: string }> },
) {
  const jobUuid = encodeURIComponent((await params).jobUuid);

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/admin/analysis-results/${jobUuid}`, {
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const payload = parsePayload(await response.text());
    if ((response.status === 401 || response.status === 403) && process.env.NODE_ENV !== "production") {
      const rawJobUuid = decodeURIComponent(jobUuid);
      const [detail, structured_ingest] = await Promise.all([
        getLocalJobDetail(rawJobUuid),
        getLocalStructuredIngest(rawJobUuid).catch(() => ({})),
      ]);
      return NextResponse.json({ ...detail, structured_ingest, source: "local-fallback" }, { status: 200 });
    }
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    console.error("Failed to fetch persisted analysis result detail:", error);
    return NextResponse.json(
      { detail: "failed to fetch persisted analysis result detail" },
      { status: 500 },
    );
  }
}
