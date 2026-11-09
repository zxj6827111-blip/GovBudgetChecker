import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";
export async function GET(_request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  try {
    const upstream = await fetchWithTimeout(
      `${apiBase}/api/jobs/${encodeURIComponent((await params).job_id)}/reanalyze-options`,
      { cache: "no-store", headers: auth.headers }
    );
    return new NextResponse(await upstream.text(), { status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" } });
  } catch { return NextResponse.json({ detail: "检查方式配置服务暂时不可用" }, { status: 503 }); }
}