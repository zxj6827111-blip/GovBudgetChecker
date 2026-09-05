import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

/**
 * /api/metrics 代理路由。
 *
 * 背景：质量管理页（QualityPage.tsx）以相对路径 fetch("/api/metrics")，请求落在
 * Next 源本身，而此前后端 /api/metrics（api/routes/metrics.py，require_admin）
 * 没有对应的代理路由——浏览器端始终 404，指标卡只能显示"指标端点暂不可用"。
 * e2e 用 page.route mock 了 /api/metrics，所以单看 e2e 发现不了这个缺口；
 * 真实环境走查（2026-08-29）暴露后按 workflow 代理的既有三段式补齐
 * （requireBackendAuthHeaders 注入 X-API-Key + X-Session-Token，后端
 * authorize_metrics_request 支持 scrape_token / admin_session 两种通过方式）。
 *
 * 首次采集在大语料上可能超过默认 apiTimeout（需扫描全部 status.json），
 * 因此这里显式放宽到 30s；后端有 TTL 缓存，常规请求仍是毫秒级。
 * 透传查询串以支持 format=prom（Prometheus 文本）。
 */
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const search = request.nextUrl.search || "";
  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/metrics${search}`,
      {
        cache: "no-store",
        headers: auth.headers,
      },
      30_000,
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Failed to fetch metrics:", error);
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}
