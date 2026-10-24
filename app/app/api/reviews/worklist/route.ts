import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/** 清单失败必须显示不可用，不能把数据库异常伪装成零待办。 */
export async function GET(_request: NextRequest): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/reviews/worklist`, {
      cache: "no-store", headers: auth.headers,
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "review service unavailable" }, { status: 502 });
  }
}
