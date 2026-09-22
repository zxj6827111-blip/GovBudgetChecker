import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 材料台账接口的 Next 代理层。
 *
 * 只做三件事：带上后端会话令牌转发、原样透传查询串、原样透传状态码与响应体。
 *
 * 刻意**不做**本地兜底数据（其他页面有 local-fallback 演示数据）：台账里凭空
 * 出现的材料会被当真，宁可让页面显示"加载失败 + 重试"。
 * 也刻意不改写错误体：后端 403/404/422 的 `detail` 是页面判断
 * "没权限 / 不存在 / 参数非法"的唯一依据，包装成 200 会让这些区别消失。
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
    console.error("materials coverage proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料台账服务暂时不可用" }, { status: 502 });
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxy(request, "/api/materials/coverage");
}
