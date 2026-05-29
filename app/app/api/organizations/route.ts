import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalOrganizationsTree, invalidateLocalDataCache } from "@/lib/localData";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

function payloadCount(payload: { total?: unknown; tree?: unknown }): number {
  const total = Number(payload.total);
  if (Number.isFinite(total)) {
    return total;
  }
  return Array.isArray(payload.tree) ? payload.tree.length : 0;
}

function shouldPreferLocalOrganizations(
  upstream: { total?: unknown; tree?: unknown },
  localData: { total?: unknown; tree?: unknown },
): boolean {
  const upstreamCount = payloadCount(upstream);
  const localCount = payloadCount(localData);
  return localCount > 0 && upstreamCount < localCount && upstreamCount <= Math.max(5, Math.floor(localCount / 4));
}

export async function GET(request: Request) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const requestUrl = new URL(request.url);
  const statsMode = String(requestUrl.searchParams.get("stats") ?? "").trim().toLowerCase();
  const useLightLocalTree = ["none", "false", "0", "off"].includes(statsMode);

  try {
    const upstreamUrl = new URL(`${apiBase}/api/organizations`);
    requestUrl.searchParams.forEach((value, key) => {
      upstreamUrl.searchParams.set(key, value);
    });

    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      headers: auth.headers,
      cache: "no-store",
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { tree: [], total: 0 };
    }
    if (response.ok) {
      const localData = await getLocalOrganizationsTree({ stats: !useLightLocalTree });
      if (shouldPreferLocalOrganizations(data, localData)) {
        if (process.env.NODE_ENV !== "production") {
          console.warn(
            "Using local organization catalog because backend returned fewer organizations.",
            { backend_total: payloadCount(data), local_total: payloadCount(localData) },
          );
        }
        return NextResponse.json(localData, { status: 200 });
      }
      return NextResponse.json(data, { status: response.status });
    }
  } catch (error) {
    if (process.env.NODE_ENV !== "production") {
      console.error("Failed to fetch organizations:", error);
    }
  }

  const localData = await getLocalOrganizationsTree({ stats: !useLightLocalTree });
  return NextResponse.json(localData, { status: 200 });
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const response = await fetchWithTimeout(`${apiBase}/api/organizations`, {
      method: "POST",
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    if (response.ok) {
      invalidateLocalDataCache();
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to create organization:", error);
    return NextResponse.json(
      { error: "Failed to create organization" },
      { status: 500 }
    );
  }
}
