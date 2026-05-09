import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const runtime = "nodejs";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ versionId: string }> }
) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  try {
    let body: any = undefined;
    try {
      body = await req.json();
    } catch {
      body = undefined;
    }
    const headers = new Headers(auth.headers);
    if (body) {
      headers.set("Content-Type", "application/json");
    }

    const upstream = await fetch(
      `${apiBase}/api/documents/${encodeURIComponent((await params).versionId)}/run`,
      {
        method: "POST",
        headers,
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
