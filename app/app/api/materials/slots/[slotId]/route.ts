import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 材料详情接口的 Next 代理层（WP2-B）。
 *
 * 与 WP2-A 的三个代理同一手法：带上后端会话令牌转发、原样透传查询串、
 * 原样透传状态码与响应体，**不做本地兜底数据**。
 *
 * 这一点在本页尤其重要：材料详情里的"当前文件版本""正式问题数""检查覆盖"
 * 都是可以被当成依据去下结论的数字。任何本地兜底或错误体改写都会让
 * "后端 403 / 后端没数据"看起来像"这份材料没问题"。
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
    console.error("material detail proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料详情服务暂时不可用" }, { status: 502 });
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ slotId: string }> },
): Promise<NextResponse> {
  const { slotId } = await context.params;
  return proxy(request, `/api/materials/slots/${encodeURIComponent(slotId)}`);
}
