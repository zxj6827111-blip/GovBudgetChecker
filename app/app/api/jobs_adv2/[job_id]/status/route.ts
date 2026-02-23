import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export const runtime = "nodejs";

export async function GET(
  _req: NextRequest,
  { params }: { params: { job_id: string } }
) {
  try {
    const upstream = await fetch(
      `${apiBase}/api/jobs/${encodeURIComponent(params.job_id)}/status`,
      {
        cache: "no-store",
        headers: backendAuthHeaders(),
      }
    );
    const text = await upstream.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: upstream.status });
  } catch (e: any) {
    return NextResponse.json(
      { error: e?.message || String(e) },
      { status: 500 }
    );
  }
}

