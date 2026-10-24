import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/activity${request.nextUrl.search}`, {
      cache: "no-store",
      headers: auth.headers,
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}
