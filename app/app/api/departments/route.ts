import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function GET() {
  try {
    const response = await fetchWithTimeout(`${apiBase}/api/departments`, {
      cache: "no-store",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { departments: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch departments:", error);
    return NextResponse.json(
      { error: "backend_unavailable", departments: [], total: 0 },
      { status: 502 }
    );
  }
}
