import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";
import {
  LocalDataError,
  getLocalOrganizationJobs,
} from "@/lib/localData";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: { org_id: string } }
) {
  const orgId = encodeURIComponent(params.org_id);
  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBase}/api/organizations/${orgId}/jobs`);
  requestUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });
  try {
    const res = await fetchWithTimeout(upstreamUrl.toString(), {
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
    if (res.ok) {
      return NextResponse.json(data, { status: res.status });
    }
  } catch (error) {
    console.error("Failed to fetch organization jobs:", error);
  }

  try {
    const localData = await getLocalOrganizationJobs(params.org_id, {
      include_children: requestUrl.searchParams.get("include_children") === "true",
      limit: requestUrl.searchParams.get("limit")
        ? Number(requestUrl.searchParams.get("limit"))
        : null,
      offset: requestUrl.searchParams.get("offset")
        ? Number(requestUrl.searchParams.get("offset"))
        : 0,
    });
    return NextResponse.json(localData, { status: 200 });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json({ detail: error.message, jobs: [] }, { status: error.status });
    }
    console.error("Failed to read local organization jobs:", error);
    return NextResponse.json({ detail: "Failed to load organization jobs", jobs: [] }, { status: 500 });
  }
}
