import { NextRequest } from "next/server";

import { proxyObligationDecision } from "@/lib/reviewActionProxy";

export const dynamic = "force-dynamic";

/**
 * `PUT /api/reviews/{slot_id}/obligations/{obligation_id}` 的 Next 代理（WP3-B）。
 *
 * 转发实现只有一份（`@/lib/reviewActionProxy`）：与三个复核写动作同一份
 * "原样透传请求体与状态码"的纪律，避免某条路径把 409 的业务原因吞掉。
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string; obligationId: string }> },
): Promise<Response> {
  const resolved = await params;
  return proxyObligationDecision(request, resolved.slotId, resolved.obligationId);
}
