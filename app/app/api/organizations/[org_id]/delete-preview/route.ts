import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function GET(
  _request: Request,
  { params }: { params: { org_id: string } }
) {
  try {
    const orgId = encodeURIComponent(params.org_id);
    const response = await fetchWithTimeout(
      `${apiBase}/api/organizations/${orgId}/delete-preview`,
      {
        headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
        cache: "no-store",
      }
    );
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || "invalid backend response" };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch organization delete preview:", error);
    return NextResponse.json(
      { detail: "failed to fetch delete preview" },
      { status: 500 }
    );
  }
}
