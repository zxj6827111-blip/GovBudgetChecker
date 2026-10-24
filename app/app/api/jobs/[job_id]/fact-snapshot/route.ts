import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  const jobId = encodeURIComponent((await params).job_id);
  const url = new URL(`${apiBase}/api/jobs/${jobId}/fact-snapshot`);
  for (const key of ["revision_id", "view", "offset", "limit"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value !== null) url.searchParams.set(key, value);
  }
  try {
    const upstream = await fetchWithTimeout(url.toString(), { cache: "no-store", headers: auth.headers });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "事实版本服务暂时不可用" }, { status: 503 });
  }
}
