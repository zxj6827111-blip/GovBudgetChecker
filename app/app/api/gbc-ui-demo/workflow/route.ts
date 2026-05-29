import { NextRequest, NextResponse } from "next/server";

import {
  createRemediationPackage,
  readIssueWorkflowState,
  updateIssueWorkflow,
} from "@/lib/issueWorkflowStore";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const dynamic = "force-dynamic";

export async function GET() {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const state = await readIssueWorkflowState();
  return NextResponse.json(state, { status: 200 });
}

export async function POST(request: NextRequest) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
  const action = String(body.action ?? "").trim();

  try {
    if (action === "update_issue") {
      const state = await updateIssueWorkflow({
        job_id: String(body.job_id ?? ""),
        issue_id: String(body.issue_id ?? ""),
        status: body.status as any,
        title: typeof body.title === "string" ? body.title : undefined,
        severity: typeof body.severity === "string" ? body.severity : undefined,
        page: Number.isFinite(Number(body.page)) ? Number(body.page) : null,
        organization_id: body.organization_id == null ? null : String(body.organization_id),
        organization_name: body.organization_name == null ? null : String(body.organization_name),
        note: typeof body.note === "string" ? body.note : undefined,
      });
      return NextResponse.json(state, { status: 200 });
    }

    if (action === "create_package") {
      const result = await createRemediationPackage({
        name: typeof body.name === "string" ? body.name : undefined,
        organization_id: body.organization_id == null ? null : String(body.organization_id),
        organization_name: body.organization_name == null ? null : String(body.organization_name),
        job_ids: Array.isArray(body.job_ids) ? body.job_ids.map((item) => String(item)) : [],
        issue_keys: Array.isArray(body.issue_keys) ? body.issue_keys.map((item) => String(item)) : [],
      });
      return NextResponse.json(result, { status: 200 });
    }

    return NextResponse.json({ detail: "unsupported action" }, { status: 400 });
  } catch (error) {
    return NextResponse.json(
      { detail: error instanceof Error ? error.message : "workflow update failed" },
      { status: 400 },
    );
  }
}
