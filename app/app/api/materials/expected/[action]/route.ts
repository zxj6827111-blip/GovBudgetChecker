import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ action: string }> };

async function proxy(request: NextRequest, context: Context) {
  const { action } = await context.params;
  if ((request.method === "GET" && action !== "template") ||
      (request.method === "POST" && !["preview", "import"].includes(action))) {
    return NextResponse.json({ detail: "接口不存在" }, { status: 404 });
  }
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) return auth.response;
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/materials/expected/${action}`, {
      method: request.method, headers: auth.headers, cache: "no-store",
      ...(request.method === "POST" ? { body: await request.text() } : {}),
    }, 30000);
    const headers = new Headers();
    for (const key of ["Content-Type", "Content-Disposition"]) {
      const value = response.headers.get(key);
      if (value) headers.set(key, value);
    }
    return new NextResponse(await response.text(), { status: response.status, headers });
  } catch {
    return NextResponse.json({ detail: "应收材料服务暂时不可用" }, { status: 502 });
  }
}
export const GET = proxy;
export const POST = proxy;
