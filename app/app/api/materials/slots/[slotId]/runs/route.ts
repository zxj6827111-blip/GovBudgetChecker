import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 槽位处理记录的 Next 代理层。
 *
 * 与详情、版本历史同一手法：原样透传状态码与响应体。
 * 后端已在 meta 里给出 `linkage_basis` / `legacy_unlinked_runs_excluded`，
 * 前端据此说明"这份记录是按什么关联出来的"，不自行推断。
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
    console.error("material runs proxy failed", {
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
  return proxy(request, `/api/materials/slots/${encodeURIComponent(slotId)}/runs`);
}
