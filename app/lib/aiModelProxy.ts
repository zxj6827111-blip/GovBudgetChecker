import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function proxyAiModelRequest(req: NextRequest, path: string) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  try {
    const headers = new Headers(auth.headers);
    const body = req.method === "GET" ? undefined : JSON.stringify(await req.json());
    if (body) headers.set("Content-Type", "application/json");
    const timeoutMs = path.endsWith("/test") ? 305000 : 15000;
    const upstream = await fetch(`${apiBase}${path}`, {
      method: req.method, headers, body, cache: "no-store", signal: AbortSignal.timeout(timeoutMs),
    });
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json({ detail: "无法连接模型配置服务，请稍后重试" }, { status: 502 });
  }
}
