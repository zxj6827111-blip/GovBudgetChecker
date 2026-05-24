import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const body = await request.json();
    const response = await fetchWithTimeout(
      `${apiBase}/api/jobs/${encodeURIComponent((await params).job_id)}/associate`,
      {
        method: "POST",
        headers: auth.headers,
        body: JSON.stringify(body),
      }
    );
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to associate job:", error);
    return NextResponse.json(
      { success: false, error: "Association failed" },
      { status: 500 }
    );
  }
}
