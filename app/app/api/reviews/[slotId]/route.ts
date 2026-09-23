import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 槽位级复核读接口的 Next 代理层（WP3-A）。
 *
 * 写动作在 `start` / `complete` / `reopen` 三个子路径上，不在这里——
 * 一条 URL 承担多个动作会让"到底调用了哪个动作"只能从请求体里看出来，
 * 而审计与排障都需要从路径就能读出来。
 *
 * 原样透传状态码与响应体：`403`（别人的材料）与 `404`（材料不存在）
 * 必须能被前端区分，用户要做的动作完全不同。
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string }> },
): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const slotId = encodeURIComponent((await params).slotId);
  const upstreamUrl = new URL(`${apiBase}/api/reviews/${slotId}`);
  new URL(request.url).searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: auth.headers,
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Failed to fetch review state:", error);
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}
