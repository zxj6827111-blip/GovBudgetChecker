import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import {
  LocalDataError,
  getLocalDepartmentUnits,
  invalidateLocalDataCache,
} from "@/lib/localData";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ dept_id: string }> }
) {
  const auth = await requireBackendAuthHeaders({
    "Content-Type": "application/json",
  });
  if (!auth.ok) {
    return auth.response;
  }

  const deptId = encodeURIComponent((await params).dept_id);
  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/departments/${deptId}/units`,
      {
        cache: "no-store",
        headers: auth.headers,
      }
    );
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { units: [], total: 0 };
    }
    if (response.ok) {
      return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to fetch department units:", error);
  }

  try {
    const localData = await getLocalDepartmentUnits((await params).dept_id);
    return NextResponse.json(localData, { status: 200 });
  } catch (error) {
    if (error instanceof LocalDataError) {
      return NextResponse.json({ detail: error.message, units: [], total: 0 }, { status: error.status });
    }
    console.error("Failed to read local department units:", error);
    return NextResponse.json({ detail: "Failed to load department units", units: [], total: 0 }, { status: 500 });
  }
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ dept_id: string }> }
) {
  const deptId = String((await params).dept_id || "").trim();
  if (!deptId) {
    return NextResponse.json({ error: "dept_id is required" }, { status: 400 });
  }

  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 });
  }

  const name = String(body?.name || "").trim();
  if (!name) {
    return NextResponse.json({ error: "name is required" }, { status: 400 });
  }

  try {
    const response = await fetchWithTimeout(`${apiBase}/api/organizations`, {
      method: "POST",
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        name,
        level: "unit",
        parent_id: deptId,
      }),
    });
    const text = await response.text();
    let data: any;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
    if (response.ok) {
      invalidateLocalDataCache();
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to create department unit:", error);
    return NextResponse.json(
      { error: "Failed to create department unit" },
      { status: 500 }
    );
  }
}
