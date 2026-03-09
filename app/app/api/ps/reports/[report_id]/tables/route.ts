import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(
  request: NextRequest,
  { params }: { params: { report_id: string } }
) {
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(
    `${apiBase}/api/ps/reports/${encodeURIComponent(params.report_id)}/tables`
  );
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
      data = { report_id: params.report_id, items: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch PS report tables:", error);
    return NextResponse.json(
      { report_id: params.report_id, items: [], total: 0 },
      { status: 200 }
    );
  }
}
