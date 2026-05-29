import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalOrganizationsList } from "@/lib/localData";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

function payloadCount(payload: { total?: unknown; organizations?: unknown }): number {
  const total = Number(payload.total);
  if (Number.isFinite(total)) {
    return total;
  }
  return Array.isArray(payload.organizations) ? payload.organizations.length : 0;
}

function shouldPreferLocalOrganizations(
  upstream: { total?: unknown; organizations?: unknown },
  localData: { total?: unknown; organizations?: unknown },
): boolean {
  const upstreamCount = payloadCount(upstream);
  const localCount = payloadCount(localData);
  return localCount > 0 && upstreamCount < localCount && upstreamCount <= Math.max(5, Math.floor(localCount / 4));
}

export async function GET() {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/list`, {
      headers: auth.headers,
      cache: "no-store",
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { organizations: [] };
    }
    if (response.ok) {
      const localData = await getLocalOrganizationsList();
      if (shouldPreferLocalOrganizations(data, localData)) {
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            "Using local organization list because backend returned fewer organizations.",
            { backend_total: payloadCount(data), local_total: payloadCount(localData) },
          );
        }
        return NextResponse.json(localData, { status: 200 });
      }
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    if (process.env.NODE_ENV !== "production") {
      console.error("Failed to fetch organizations list:", error);
    }
  }

  const localData = await getLocalOrganizationsList();
  return NextResponse.json(localData, { status: 200 });
}
