import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }

  try {
    const formData = await req.formData();
    const upstream = await fetch(`${apiBase}/api/documents/upload`, {
      method: "POST",
      headers: auth.headers,
      body: formData as any,
    });

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
