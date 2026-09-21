import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/** 区级主管部门矩阵的代理层（口径与 /api/materials/coverage 一致，见该文件注释）。 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ districtId: string }> },
): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) {
    return auth.response;
  }

  const districtId = encodeURIComponent((await params).districtId);
  const upstreamUrl = new URL(`${apiBase}/api/materials/districts/${districtId}/departments`);
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
    console.error("materials district proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料台账服务暂时不可用" }, { status: 502 });
  }
}
