import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/** 部门材料矩阵的代理层（口径与 /api/materials/coverage 一致，见该文件注释）。 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ departmentId: string }> },
): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) {
    return auth.response;
  }

  const departmentId = encodeURIComponent((await params).departmentId);
  const upstreamUrl = new URL(`${apiBase}/api/materials/departments/${departmentId}/matrix`);
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
    console.error("materials department proxy failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "材料台账服务暂时不可用" }, { status: 502 });
  }
}
