import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export const runtime = "nodejs";

export async function POST(
  req: NextRequest,
  { params }: { params: { job_id: string } }
) {
  try {
    let body: any = undefined;
    try {
      body = await req.json();
    } catch {
      body = undefined;
    }

    const upstream = await fetch(
      `${apiBase}/api/analyze2/${encodeURIComponent(params.job_id)}`,
      {
        method: "POST",
        headers: backendAuthHeaders(
          body ? { "Content-Type": "application/json" } : undefined
        ),
        body: body ? JSON.stringify(body) : undefined,
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

