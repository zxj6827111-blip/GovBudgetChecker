import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";

export const runtime = "nodejs";

export async function GET() {
  try {
    const res = await fetch(`${apiBase}/health`, {
      cache: "no-store",
      headers: backendAuthHeaders(),
    });
    const text = await res.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (e: any) {
    return NextResponse.json(
      { status: "down", error: e?.message || String(e) },
      { status: 502 }
    );
  }
}

