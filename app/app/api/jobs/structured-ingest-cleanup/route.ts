import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetchWithTimeout(
      `${apiBase}/api/jobs/structured-ingest-cleanup`,
      {
        method: "POST",
        headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
        body: JSON.stringify(body ?? {}),
      }
    );
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to cleanup structured ingest history:", error);
    return NextResponse.json(
      { success: false, error: "Structured ingest cleanup failed" },
      { status: 500 }
    );
  }
}
