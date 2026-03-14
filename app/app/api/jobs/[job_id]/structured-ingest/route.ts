import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { LocalDataError, getLocalStructuredIngest } from "@/lib/localData";

export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  { params }: { params: { job_id: string } }
) {
  const jobId = encodeURIComponent(params.job_id);
  try {
    const res = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}/structured-ingest`, {
      cache: "no-store",
      headers: backendAuthHeaders(),
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
    console.error("Failed to fetch structured ingest payload:", error);
  }

  try {
    const localData = await getLocalStructuredIngest(params.job_id);
    return NextResponse.json(localData, { status: 200 });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    console.error("Failed to read local structured ingest payload:", error);
    return NextResponse.json({ detail: "Failed to load structured ingest payload" }, { status: 500 });
  }
}
