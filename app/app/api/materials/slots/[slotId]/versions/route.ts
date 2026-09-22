import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 槽位版本历史的 Next 代理层。
 *
 * 与详情、处理记录同一手法：原样透传状态码与响应体。
 * 后端的 `is_current` 是槽位指针的唯一投影，前端不做二次判断。
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
    console.error("material versions proxy failed", {
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
  return proxy(request, `/api/materials/slots/${encodeURIComponent(slotId)}/versions`);
}
