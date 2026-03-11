import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/import`, {
      method: "POST",
      headers: backendAuthHeadersWithSession(),
      body: formData,
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
    console.error("Failed to import organizations:", error);
    return NextResponse.json(
      { success: false, error: `Import failed: ${error}` },
      { status: 500 }
    );
  }
}
