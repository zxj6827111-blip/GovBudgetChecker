import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

/**
 * /api/workflow 代理路由（Task 6.6）。
 *
 * 背景：后端 `/api/workflow`（api/routes/workflow.py，底层
 * src/services/issue_workflow_store.py）此前只有一个面向 gbc-ui-demo 单体页面
 * 的代理（app/app/api/gbc-ui-demo/workflow/route.ts），审核工作台新组件需要
 * 独立于那个即将被 Task 10 下线的单体路径调用同一个后端端点，因此新建这个
 * 与该单体无关的代理路由，写法完全参照既有 gbc-ui-demo/workflow/route.ts
 * （requireBackendAuthHeaders + fetchWithTimeout + apiBase 三段式既有模式）。
 *
 * 为什么用 /api/workflow 而不是 /api/jobs/{job_id}/issues/ignore：
 * 两者是完全独立、互不感知的存储机制（前者写 .issue_workflow.json，支持
 * pending/confirmed/no_issue/needs_review/in_package 五态 + note 备注字段；
 * 后者写 .ignored_issues.json，把问题从返回 payload 里整个过滤掉）。审核工作台
 * 需要"确认/忽略/补充意见"三态并存 + 备注能力，/api/workflow 原生支持，
 * 选它作为唯一工作流路径，不使用 issues/ignore 端点（避免两套并行写入导致
 * 状态不一致，详见本批交付说明"3. /api/workflow 与 issues/ignore 的关系判断"）。
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/workflow`, {
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
    console.error("Failed to fetch workflow:", error);
    return NextResponse.json({ detail: "backend service unavailable" }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/workflow`, {
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
    console.error("Failed to update workflow:", error);
    return NextResponse.json(
      { detail: "backend service unavailable" },
      { status: 502 },
    );
  }
}
