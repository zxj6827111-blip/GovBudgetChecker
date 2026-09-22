import { NextRequest } from "next/server";

import { proxyReviewAction } from "@/lib/reviewActionProxy";

export const dynamic = "force-dynamic";

/**
 * `POST /api/reviews/{slot_id}/start` 的 Next 代理（WP3-A）。
 *
 * 三个写动作的转发实现只有一份（`@/lib/reviewActionProxy`）：它们的差异只有
 * 目标路径，而"原样透传请求体与状态码"这件事一旦各写一份就必然漂移，
 * 漂移的后果是某个动作把 409 的 `blockers` 吞掉、用户看不到业务原因。
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string }> },
): Promise<Response> {
  return proxyReviewAction(request, (await params).slotId, "start");
}
