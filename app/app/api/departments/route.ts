import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalDepartments } from "@/lib/localData";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

import { shouldFallbackToLocal } from "@/lib/fallbackPolicy";

export async function GET() {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/departments`, {
      cache: "no-store",
      headers: auth.headers,
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { departments: [], total: 0 };
    }
    if (response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    if (shouldFallbackToLocal(response.status) && process.env.NODE_ENV !== "production") {
      console.warn("departments route falling back to local data", { status: response.status });
      const localData = await getLocalDepartments();
      return NextResponse.json(localData, {
        status: 200,
        headers: {
          "X-Data-Source": "local-fallback",
          "X-Fallback-Reason": String(response.status),
        },
      });
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("departments route backend request failed", {
      error: error instanceof Error ? error.message : String(error),
    });
  }

  return NextResponse.json(
    { detail: "backend is unavailable and no local fallback configured for departments" },
    { status: 502 },
  );
}
