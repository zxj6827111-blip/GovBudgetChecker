import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function POST(request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) return auth.response;
  const jobId = encodeURIComponent((await params).job_id);
  try {
    const body = await request.json().catch(() => ({}));
    const upstream = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}/corrections`, {
      method: "POST",
      headers: auth.headers,
      body: JSON.stringify(body ?? {}),
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "校正提交失败，请稍后重试" }, { status: 503 });
  }
}
