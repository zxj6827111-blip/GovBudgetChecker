import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

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
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch organizations list:", error);
    return NextResponse.json({ organizations: [] }, { status: 200 });
  }
}

