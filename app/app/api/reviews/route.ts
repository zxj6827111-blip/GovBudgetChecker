import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

/**
 * 复核接口的 Next 代理层（WP3-A）——按任务解析复核上下文。
 *
 * `GET /api/reviews?job_uuid=...`：审核工作台的入口仍然是 `/review?job=<uuid>`，
 * 页面只知道 `job_id`，必须由**服务端**解析到材料槽位。解析链路只有一条：
 * `structured_ingest.document_version_id → fiscal_document_versions.slot_id`。
 * 前端不参与这次解析，也不允许按文件名/组织名/时间接近猜归属——
 * 猜错一次就会把别的单位、别的年度的分析结论挂到这条材料上，而它看起来完全正常。
 *
 * 与 WP2 的材料代理同一手法：带后端会话令牌转发、原样透传查询串、
 * **原样透传状态码与响应体**，不做任何本地兜底。这一点在复核上尤其重要：
 * 409 里的 `blockers` 是"为什么不能完成复核"的唯一答案，
 * 任何改写（例如统一成"操作失败"）都会把唯一有用的信息丢掉。
 *
 * 写动作（start/complete/reopen）**不在这条路径上**：它们必须带槽位 id，
 * 因为服务端要先按槽位做授权。Next 对未导出的方法直接返回 405。
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  const upstreamUrl = new URL(`${apiBase}/api/reviews`);
  new URL(request.url).searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.set(key, value);
  });

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      cache: "no-store",
      headers: auth.headers,
    });
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Failed to fetch review context:", error);
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}
