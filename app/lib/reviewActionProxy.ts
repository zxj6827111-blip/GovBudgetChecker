import type { NextRequest } from "next/server";

import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

/** 复核的三个写动作。取值域与后端路径一致，新增动作必须两侧同时改。 */
export type ReviewAction = "start" | "complete" | "reopen";

/**
 * 复核写动作的统一代理（WP3-A）。
 *
 * 为什么三个动作共用一份实现
 * --------------------------
 * 它们的差异只有目标路径。而"原样透传请求体 + 原样透传状态码与响应体"
 * 只要各写一份就必然漂移——漂移的后果是某个动作把 `409` 的 `blockers`
 * 吞掉，界面退化成一句"操作失败"，用户永远不知道下一步做什么。
 *
 * 请求体用 `text()` 原样转发而不是解析后重组：解析再序列化会在出现未知字段时
 * 把它们悄悄丢掉，而"前端到底传了什么"本身正是服务端要核对的内容（§二十八）。
 */
export async function proxyReviewAction(
  request: NextRequest,
  slotId: string,
  action: ReviewAction,
): Promise<Response> {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) {
    return auth.response;
  }

  const encoded = encodeURIComponent(slotId);
  const upstreamUrl = new URL(`${apiBase}/api/reviews/${encoded}/${action}`);

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      method: "POST",
      cache: "no-store",
      headers: auth.headers,
      body: await request.text(),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Review action proxy failed", {
      action,
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "review service unavailable" }, { status: 502 });
  }
}

/**
 * 人工补核写动作的代理（WP3-B）：`PUT /api/reviews/{slotId}/obligations/{obligationId}`。
 *
 * 与 ``proxyReviewAction`` 共用同一份透传纪律（请求体原样、状态码原样、
 * 409 的 blockers / current_revision 不许被吞），只是方法与路径形态不同——
 * 义务 id 进路径，决定进请求体。
 */
export async function proxyObligationDecision(
  request: NextRequest,
  slotId: string,
  obligationId: string,
): Promise<Response> {
  const auth = await requireBackendAuthHeaders({ "Content-Type": "application/json" });
  if (!auth.ok) {
    return auth.response;
  }

  const upstreamUrl = new URL(
    `${apiBase}/api/reviews/${encodeURIComponent(slotId)}/obligations/${encodeURIComponent(obligationId)}`,
  );

  try {
    const response = await fetchWithTimeout(upstreamUrl.toString(), {
      method: "PUT",
      cache: "no-store",
      headers: auth.headers,
      body: await request.text(),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    console.error("Obligation decision proxy failed", {
      obligation_id: obligationId,
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ detail: "review service unavailable" }, { status: 502 });
  }
}
