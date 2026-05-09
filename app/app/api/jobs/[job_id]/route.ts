import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { LocalDataError, getLocalJobDetail } from "@/lib/localData";
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
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    if (res.ok) {
      return NextResponse.json(data, { status: res.status });
    }
  } catch (error) {
    console.error("Failed to fetch job detail:", error);
  }

  try {
    const localData = await getLocalJobDetail((await params).job_id);
    return NextResponse.json(localData, { status: 200 });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    console.error("Failed to read local job detail:", error);
    return NextResponse.json({ detail: "Failed to load job detail" }, { status: 500 });
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
