import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  const jobId = encodeURIComponent((await params).job_id);
  try {
    // R6（2026-11-20 复审）：历史读取校正必须把 analysis_revision 透传到后端，
    // 否则代理永远返回当前版本，历史页串入新版校正记录。
    const search = request.nextUrl.search || "";
    const upstream = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}/correction-targets${search}`, {
      cache: "no-store",
      headers: auth.headers,
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "校正服务暂时不可用" }, { status: 503 });
  }
}
