import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalJobs } from "@/lib/localData";

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
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const payload = parsePayload(await response.text());
    if ((response.status === 401 || response.status === 403) && process.env.NODE_ENV !== "production") {
      const limit = Number(requestUrl.searchParams.get("limit") ?? 50);
      const offset = Number(requestUrl.searchParams.get("offset") ?? 0);
      const localJobs = await getLocalJobs({
        limit: Number.isFinite(limit) ? limit : 50,
        offset: Number.isFinite(offset) ? offset : 0,
      });
      const items = Array.isArray(localJobs) ? localJobs : localJobs.items;
      const total = Array.isArray(localJobs) ? localJobs.length : localJobs.total;
      return NextResponse.json({ items, total, limit, offset, source: "local-fallback" }, { status: 200 });
    }
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch persisted analysis results:", error);
    const limit = Number(requestUrl.searchParams.get("limit") ?? 50);
    const offset = Number(requestUrl.searchParams.get("offset") ?? 0);
    const localJobs = await getLocalJobs({
      limit: Number.isFinite(limit) ? limit : 50,
      offset: Number.isFinite(offset) ? offset : 0,
    });
    const items = Array.isArray(localJobs) ? localJobs : localJobs.items;
    const total = Array.isArray(localJobs) ? localJobs.length : localJobs.total;
    return NextResponse.json({ items, total, limit, offset, source: "local-fallback" }, { status: 200 });
  }
}
