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

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBase}/api/admin/analysis-results`);
  requestUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const payload = parsePayload(await response.text());
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch persisted analysis results:", error);
    return NextResponse.json(
      { detail: "failed to fetch persisted analysis results" },
      { status: 500 },
    );
  }
}
