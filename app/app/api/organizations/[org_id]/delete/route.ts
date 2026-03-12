import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function POST(
  _request: Request,
  { params }: { params: { org_id: string } }
) {
  try {
    const orgId = encodeURIComponent(params.org_id);
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/${orgId}`, {
      method: "DELETE",
      headers: backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || "invalid backend response" };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to delete organization via proxy:", error);
    return NextResponse.json(
      { detail: "failed to delete organization" },
      { status: 500 }
    );
  }
}
