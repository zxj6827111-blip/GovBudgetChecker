import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { getLocalDepartments } from "@/lib/localData";

export const dynamic = "force-dynamic";

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
    if (response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
  } catch (error) {
    if (process.env.NODE_ENV !== "production") {
      console.error("Failed to fetch departments:", error);
    }
  }

  const localData = await getLocalDepartments();
  return NextResponse.json(localData, { status: 200 });
}
