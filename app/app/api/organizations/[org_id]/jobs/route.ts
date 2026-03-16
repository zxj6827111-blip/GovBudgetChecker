import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";
import {
  LocalDataError,
  getLocalOrganizationJobs,
} from "@/lib/localData";

export const dynamic = "force-dynamic";

type OrganizationJobsPayload = {
  jobs: unknown[];
  total?: number | null;
  limit?: number | null;
  offset?: number;
};

function isOrganizationJobsPayload(value: unknown): value is OrganizationJobsPayload {
  return Boolean(value) && typeof value === "object" && Array.isArray((value as OrganizationJobsPayload).jobs);
}

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
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
    });
    const text = await res.text();
    let data: unknown = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        console.error("Failed to parse backend organization jobs response:", {
          orgId: params.org_id,
          status: res.status,
          error,
          bodyPreview: text.slice(0, 500),
        });
      }
    }
    if (res.ok && isOrganizationJobsPayload(data)) {
      return NextResponse.json(data, { status: res.status });
    }
    if (!res.ok) {
      console.error("Backend organization jobs request failed:", {
        orgId: params.org_id,
        status: res.status,
        bodyPreview: text.slice(0, 500),
      });
    } else {
      console.error("Backend organization jobs returned unexpected payload:", {
        orgId: params.org_id,
        status: res.status,
        bodyPreview: text.slice(0, 500),
      });
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
