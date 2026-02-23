import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET() {
  const start = Date.now();
  try {
    const res = await fetch(`${apiBase}/api/config`, {
      cache: "no-store",
      headers: backendAuthHeaders(),
    });
    const data = await res.json();

    // 主后端响应耗时
    const ms = Date.now() - start;

    // 尝试探测 AI 提取服务连通性与耗时（HEAD 优先，失败则 GET），将 2xx-4xx 视为“可达”（405 表示方法不被允许，但服务在线）
    let ai_extractor_alive: boolean | null = null;
    let ai_extractor_ping_ms: number | null = null;
    const aiUrl = (data as any)?.ai_extractor_url as string | undefined;
    if (aiUrl) {
      const pingOnce = async (method: "HEAD" | "GET") => {
        const ac = new AbortController();
        const timer = setTimeout(() => ac.abort(), 3000);
        const t0 = Date.now();
        try {
          const resp = await fetch(aiUrl, { method, cache: "no-store", signal: ac.signal });
          ai_extractor_ping_ms = Date.now() - t0;
          // 认为 2xx-4xx 均为服务可达（非网络错误）
          ai_extractor_alive = resp.status < 500;
        } catch {
          ai_extractor_alive = false;
        } finally {
          clearTimeout(timer);
        }
      };
      await pingOnce("HEAD");
      if (ai_extractor_alive === false) {
        await pingOnce("GET");
      }
    }

    const normalized = {
      ...data,
      ai_assist_enabled: (data as any).ai_assist_enabled ?? (data as any).ai_enabled ?? false,
      backend_response_ms: ms,
      ai_extractor_alive,
      ai_extractor_ping_ms,
    };
    return NextResponse.json(normalized, { status: res.status });
  } catch (e: any) {
    const ms = Date.now() - start;
    return NextResponse.json(
      { error: "proxy_fetch_failed", message: String(e), backend_response_ms: ms },
      { status: 502 }
    );
  }
}
