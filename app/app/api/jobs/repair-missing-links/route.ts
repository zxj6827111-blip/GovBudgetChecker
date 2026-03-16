import { NextRequest, NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetchWithTimeout(
      `${apiBase}/api/jobs/repair-missing-links`,
      {
        method: "POST",
        headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
        body: JSON.stringify(body ?? {}),
      },
      300000,
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
    console.error("Failed to repair missing organization links:", error);
    const isTimeout = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        success: false,
        error: isTimeout
          ? "Repair timed out after 300 seconds"
          : "Repair missing links failed",
      },
      { status: isTimeout ? 504 : 500 },
    );
  }
}
