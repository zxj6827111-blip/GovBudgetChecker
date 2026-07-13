import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { invalidateLocalDataCache } from "@/lib/localData";
import { normalizeBackendError } from "@/lib/normalizeBackendError";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ org_id: string }> }
) {
  try {
    const orgId = encodeURIComponent((await params).org_id);
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/${orgId}`, {
      method: "DELETE",
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || "invalid backend response" };
    }
    if (response.ok) {
      invalidateLocalDataCache();
    }
    return NextResponse.json(normalizeBackendError(data, response.status), { status: response.status });
  } catch (error) {
    console.error("Failed to delete organization via proxy:", error);
    return NextResponse.json(
      { detail: "failed to delete organization" },
      { status: 500 }
    );
  }
}
