import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const jobId = encodeURIComponent((await params).job_id);
  try {
    const res = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}`, {
      cache: "no-store",
      headers: auth.headers,
    });
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Failed to fetch job detail:", error);
    return NextResponse.json(
      { detail: "backend service unavailable" },
      { status: 502 },
    );
  }
}

export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ job_id: string }> }
) {
  const jobId = encodeURIComponent((await params).job_id);
  try {
    const res = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}`, {
      method: "DELETE",
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
    });
    const text = await res.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("Failed to delete job:", error);
    return NextResponse.json(
      { error: "Internal server error" },
      { status: 500 }
    );
  }
}
