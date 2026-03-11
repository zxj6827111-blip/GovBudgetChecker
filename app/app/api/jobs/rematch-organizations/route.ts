import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetchWithTimeout(`${apiBase}/api/jobs/rematch-organizations`, {
      method: "POST",
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      body: JSON.stringify(body ?? {}),
    }, 300000);
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to rematch organizations:", error);
    const isTimeout = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        success: false,
        error: isTimeout
          ? "Batch organization rematch timed out after 300 seconds"
          : "Batch organization rematch failed",
      },
      { status: isTimeout ? 504 : 500 }
    );
  }
}
