import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function POST(
  request: NextRequest,
  { params }: { params: { job_id: string } }
) {
  try {
    const body = await request.json();
    const response = await fetchWithTimeout(
      `${apiBase}/api/jobs/${encodeURIComponent(params.job_id)}/associate`,
      {
        method: "POST",
        headers: backendAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
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
    console.error("Failed to associate job:", error);
    return NextResponse.json(
      { success: false, error: "Association failed" },
      { status: 500 }
    );
  }
}

