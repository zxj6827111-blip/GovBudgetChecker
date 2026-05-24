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
    const upstream = await fetch(`${apiBase}/api/documents/preflight`, {
      method: "POST",
      headers: auth.headers,
      body: formData as BodyInit,
    });

    const text = await upstream.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }

    return NextResponse.json(data, { status: upstream.status });
  } catch (error: any) {
    return NextResponse.json(
      { error: error?.message || String(error) },
      { status: 500 }
    );
  }
}
