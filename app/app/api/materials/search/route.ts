import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 全局材料搜索接口的 Next 代理层（WP2-C）。
 *
 * 与 WP2-A/B 的六个代理同一手法：带上后端会话令牌转发、原样透传查询串、
 * 原样透传状态码与响应体，**不做本地兜底数据**。
 *
 * 这一点在搜索上尤其重要：`403`（没有任何组织授权）与 `200 + items=[]`
 * （有授权但没命中）是两个完全不同的结论，前端据此决定显示"没有权限"
 * 还是"没有找到"。把错误体改写成 200 会让两者长得一样。
 */
async function proxy(request: NextRequest, upstreamPath: string): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) {
    return auth.response;
  }

  const upstreamUrl = new URL(`${apiBase}${upstreamPath}`);
  new URL(request.url).searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: auth.headers,
    });
    const text = await response.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: "后端返回了无法解析的响应" };
    }
    return NextResponse.json(data as Record<string, unknown>, { status: response.status });
  } catch (error) {
    console.error("material search proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料搜索服务暂时不可用" }, { status: 502 });
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxy(request, "/api/materials/search");
}
