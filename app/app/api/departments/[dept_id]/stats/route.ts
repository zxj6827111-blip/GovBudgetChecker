import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import {
  LocalDataError,
  getLocalDepartmentStats,
} from "@/lib/localData";

export async function GET(
  _request: Request,
  { params }: { params: { dept_id: string } }
) {
  const deptId = encodeURIComponent(params.dept_id);
  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/departments/${deptId}/stats`,
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
      data = { department_id: deptId, stats: {} };
    }
    if (response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
  } catch (error) {
    console.error("Failed to fetch department stats:", error);
  }

  try {
    const localData = await getLocalDepartmentStats(params.dept_id);
    return NextResponse.json(localData, { status: 200 });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json(
        { detail: error.message, department_id: params.dept_id, stats: {} },
        { status: error.status }
      );
    }
    console.error("Failed to read local department stats:", error);
    return NextResponse.json(
      { detail: "Failed to load department stats", department_id: params.dept_id, stats: {} },
      { status: 500 }
    );
  }
}
