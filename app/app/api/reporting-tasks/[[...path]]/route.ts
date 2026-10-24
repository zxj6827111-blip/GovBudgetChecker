import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export const dynamic = "force-dynamic";
async function proxy(request: NextRequest, context: { params: Promise<{ path?: string[] }> }) {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) return auth.response;
  const segments = (await context.params).path ?? [];
  const url = new URL(`${apiBase}/api/reporting-tasks${segments.length ? "/" + segments.map(encodeURIComponent).join("/") : ""}`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  try {
    const response = await fetchWithTimeout(url.toString(), { method: request.method, headers: auth.headers,
      body: ["POST", "PATCH"].includes(request.method) ? await request.text() : undefined, cache: "no-store" }, 60000);
    return new NextResponse(await response.text(), { status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
      ...(response.headers.get("content-disposition") ? { "Content-Disposition": response.headers.get("content-disposition")! } : {}),
    } });
  } catch {
    return NextResponse.json({ detail: "年度报送服务暂不可用，请重试" }, { status: 502 });
  }
}
export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
