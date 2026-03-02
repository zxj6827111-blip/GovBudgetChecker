import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) {
    return NextResponse.json(
      { error: "run_id_required", detail: "Query parameter run_id is required" },
      { status: 400 }
    );
  }

  try {
    const upstream = await fetch(
      `${apiBase}/api/qc/findings?run_id=${encodeURIComponent(runId)}`,
      {
        cache: "no-store",
        headers: backendAuthHeaders(),
      }
    );
    const text = await upstream.text();
    try {
      const parsed = JSON.parse(text);
      return NextResponse.json(parsed, { status: upstream.status });
    } catch {
      return NextResponse.json(
        {
          error: "non_json_from_backend",
          upstream_status: upstream.status,
          raw: text,
        },
        { status: upstream.status || 502 }
      );
    }
  } catch (error: any) {
    return NextResponse.json(
      {
        error: "backend_unavailable",
        detail: error?.message || String(error),
      },
      { status: 502 }
    );
  }
}
