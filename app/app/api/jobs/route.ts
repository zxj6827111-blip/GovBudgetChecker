import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBase}/api/jobs`);
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

    if (!res.ok) {
      if (requestUrl.searchParams.has("limit") || requestUrl.searchParams.has("offset")) {
        return NextResponse.json({ items: [], total: 0, limit: null, offset: 0 }, { status: res.status });
      }
      return NextResponse.json([], { status: res.status });
    }

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

    if (requestUrl.searchParams.has("limit") || requestUrl.searchParams.has("offset")) {
      return NextResponse.json({ items: [], total: 0, limit: null, offset: 0 }, { status: res.status });
    }
    return NextResponse.json([], { status: res.status });
  } catch (error) {
    console.error("Failed to fetch jobs:", error);
    if (requestUrl.searchParams.has("limit") || requestUrl.searchParams.has("offset")) {
      return NextResponse.json({ error: "backend_unavailable", items: [], total: 0, limit: null, offset: 0 }, { status: 502 });
    }
    return NextResponse.json({ error: "backend_unavailable", jobs: [] }, { status: 502 });
  }
}
