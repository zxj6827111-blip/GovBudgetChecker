import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) {
    return NextResponse.json([], { status: 200 });
  }

  try {
    const upstream = await fetch(
      `${apiBase}/api/qc/findings?run_id=${encodeURIComponent(runId)}`,
      {
        cache: "no-store",
        headers: backendAuthHeaders(),
      }
    );
    if (!upstream.ok) {
      return NextResponse.json([], { status: 200 });
    }
    const text = await upstream.text();
    try {
      return NextResponse.json(JSON.parse(text), { status: 200 });
    } catch {
      return NextResponse.json([], { status: 200 });
    }
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}

