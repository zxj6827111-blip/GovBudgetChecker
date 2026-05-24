import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalOrganizationsTree, invalidateLocalDataCache } from "@/lib/localData";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET() {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/organizations`, {
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
      return NextResponse.json(data, { status: response.status });
    }
  } catch (error) {
    if (process.env.NODE_ENV !== "production") {
      console.error("Failed to fetch organizations:", error);
    }
  }

  const localData = await getLocalOrganizationsTree();
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
