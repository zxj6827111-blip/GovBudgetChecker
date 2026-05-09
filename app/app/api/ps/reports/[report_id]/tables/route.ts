import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ report_id: string }> }
) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(
    `${apiBase}/api/ps/reports/${encodeURIComponent((await params).report_id)}/tables`
  );
  requestUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: auth.headers,
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { report_id: (await params).report_id, items: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch PS report tables:", error);
    return NextResponse.json(
      { report_id: (await params).report_id, items: [], total: 0 },
      { status: 200 }
    );
  }
}
