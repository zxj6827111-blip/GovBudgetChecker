import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(request: NextRequest) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBase}/api/ps/reports`);
  requestUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { items: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch PS reports:", error);
    return NextResponse.json(
      { items: [], total: 0, limit: 0, offset: 0 },
      { status: 200 }
    );
  }
}
