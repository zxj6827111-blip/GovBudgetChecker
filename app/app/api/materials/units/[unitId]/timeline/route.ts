import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 单位多年度时间轴的 Next 代理层。
 *
 * 与 WP2-A 的区级/部门矩阵同一手法：原样透传状态码与响应体。
 * 后端的 403（单位不在可见范围）与 404（单位不存在）必须保持可分，
 * 包装成 200 会让页面把"没权限"显示成"没有材料"。
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
    console.error("unit timeline proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料台账服务暂时不可用" }, { status: 502 });
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ unitId: string }> },
): Promise<NextResponse> {
  const { unitId } = await context.params;
  return proxy(request, `/api/materials/units/${encodeURIComponent(unitId)}/timeline`);
}
