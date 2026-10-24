import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

/**
 * /api/rules/{version|entries|engine-catalog} 代理路由。
 *
 * 背景：「规则与版本」页（RulesPage.tsx）以相对路径请求 /api/rules/version 与
 * /api/rules/entries，后端 api/routes/rules.py（require_admin）早已提供，但 Next
 * 层一直没有对应代理，真实环境始终 404，页面只能显示"端点暂不可用"。
 * e2e 用 page.route mock 了这两个接口，所以单看 e2e 发现不了这个缺口
 * （与 /api/metrics 当初的缺口同类，见 app/api/metrics/route.ts）。
 *
 * 只放行后端已有的三个只读端点，其余路径直接 404，不做泛化转发。
 */
export const dynamic = "force-dynamic";

const ALLOWED_ENDPOINTS = new Set(["version", "entries", "engine-catalog"]);

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ endpoint: string }> },
) {
  const { endpoint } = await context.params;
  if (!ALLOWED_ENDPOINTS.has(endpoint)) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404 });
  }

  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const search = request.nextUrl.search || "";
  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/rules/${endpoint}${search}`,
      {
        cache: "no-store",
        headers: auth.headers,
      },
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error(`Failed to fetch rules ${endpoint}:`, error);
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}
