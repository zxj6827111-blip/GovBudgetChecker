import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(
  _request: Request,
  { params }: { params: { org_id: string } }
) {
  const orgId = encodeURIComponent(params.org_id);
  try {
    const res = await fetchWithTimeout(`${apiBase}/api/organizations/${orgId}/jobs`, {
      cache: "no-store",
      headers: backendAuthHeaders(),
    });
    const text = await res.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { jobs: [] };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("Failed to fetch organization jobs:", error);
    return NextResponse.json({ jobs: [] }, { status: 200 });
  }
}

