import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetchWithTimeout(`${apiBase}/api/jobs/batch-delete`, {
      method: "POST",
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      body: JSON.stringify(body ?? {}),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to batch delete jobs:", error);
    return NextResponse.json(
      { success: false, error: "Batch job delete failed" },
      { status: 500 }
    );
  }
}
