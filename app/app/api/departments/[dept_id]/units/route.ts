import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";

export async function GET(
  _request: Request,
  { params }: { params: { dept_id: string } }
) {
  const deptId = encodeURIComponent(params.dept_id);
  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/departments/${deptId}/units`,
      {
        cache: "no-store",
        headers: backendAuthHeaders({ "Content-Type": "application/json" }),
      }
    );
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { units: [], total: 0 };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch department units:", error);
    return NextResponse.json(
      { error: "backend_unavailable", units: [], total: 0 },
      { status: 502 }
    );
  }
}
