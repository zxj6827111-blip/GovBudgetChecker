import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { getLocalJobs } from "@/lib/localData";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBase}/api/jobs`);
  const hasPagination =
    requestUrl.searchParams.has("limit") || requestUrl.searchParams.has("offset");
  requestUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const res = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
    });
    const text = await res.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }

    if (res.ok) {
      if (Array.isArray(data)) {
        return NextResponse.json(data, { status: res.status });
      }
      if (data && typeof data === "object" && Array.isArray(data.items)) {
        return NextResponse.json(
          {
            items: data.items,
            total: Number(data.total ?? data.items.length),
            limit: data.limit ?? null,
            offset: Number(data.offset ?? 0),
          },
          { status: res.status }
        );
      }
      if (!hasPagination && Array.isArray(data?.jobs)) {
        return NextResponse.json(data.jobs, { status: res.status });
      }
    }
  } catch (error) {
    console.error("Failed to fetch jobs:", error);
  }

  const limitParam = requestUrl.searchParams.get("limit");
  const offsetParam = requestUrl.searchParams.get("offset");
  const localData = await getLocalJobs({
    limit: limitParam ? Number(limitParam) : null,
    offset: offsetParam ? Number(offsetParam) : 0,
  });

  if (Array.isArray(localData)) {
    return NextResponse.json(localData, { status: 200 });
  }

  return NextResponse.json(localData, { status: 200 });
}
