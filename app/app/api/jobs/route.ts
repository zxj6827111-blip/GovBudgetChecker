import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { backendAuthHeaders } from "@/lib/backendAuth";

export async function GET() {
  try {
    const res = await fetchWithTimeout(`${apiBase}/api/jobs`, {
      cache: "no-store",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
    });
    if (!res.ok) {
      return NextResponse.json([], { status: res.status });
    }
    const data = await res.json();
    return NextResponse.json(Array.isArray(data) ? data : []);
  } catch (error) {
    console.error("Failed to fetch jobs:", error);
    return NextResponse.json([], { status: 200 });
  }
}

