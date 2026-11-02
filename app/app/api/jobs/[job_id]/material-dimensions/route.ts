import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

async function proxy(request: NextRequest, params: Promise<{ job_id: string }>) {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) return auth.response;
  try {
    const jobId = encodeURIComponent((await params).job_id);
    const upstream = await fetchWithTimeout(`${apiBase}/api/jobs/${jobId}/material-dimensions`, {
      method: request.method, headers: auth.headers, cache: "no-store",
      ...(request.method === "POST" ? { body: JSON.stringify(await request.json()) } : {}),
    });
    return new NextResponse(await upstream.text(), { status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ detail: "材料口径服务暂时不可用，请稍后重试" }, { status: 503 });
  }
}
export async function GET(request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) { return proxy(request, params); }
export async function POST(request: NextRequest, { params }: { params: Promise<{ job_id: string }> }) { return proxy(request, params); }
