import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { getLocalOrganizationsList } from "@/lib/localData";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/organizations/list`, {
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
      cache: "no-store",
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { organizations: [] };
    }
    if (response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
  } catch (error) {
    if (process.env.NODE_ENV !== "production") {
      console.error("Failed to fetch organizations list:", error);
    }
  }

  const localData = await getLocalOrganizationsList();
  return NextResponse.json(localData, { status: 200 });
}
