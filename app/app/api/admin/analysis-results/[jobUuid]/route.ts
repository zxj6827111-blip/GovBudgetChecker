import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

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
  { params }: { params: { jobUuid: string } },
) {
  const jobUuid = encodeURIComponent(params.jobUuid);

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/admin/analysis-results/${jobUuid}`, {
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const payload = parsePayload(await response.text());
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch persisted analysis result detail:", error);
    return NextResponse.json(
      { detail: "failed to fetch persisted analysis result detail" },
      { status: 500 },
    );
  }
}
