import { NextRequest } from "next/server";

import { proxyReviewAction } from "@/lib/reviewActionProxy";

export const dynamic = "force-dynamic";

/**
 * `POST /api/reviews/{slot_id}/reopen` 的 Next 代理（WP3-A）。
 *
 * 转发实现只有一份（`@/lib/reviewActionProxy`）：三个写动作的差异只有目标路径，
 * 而"原样透传请求体与状态码"各写一份必然漂移，后果是某个动作把 409 的
 * `blockers` 吞掉、用户看不到业务原因。
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string }> },
): Promise<Response> {
  return proxyReviewAction(request, (await params).slotId, "reopen");
}
