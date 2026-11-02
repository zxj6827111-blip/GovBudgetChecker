import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeadersWithSession } from "@/lib/backendAuthServer";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";

/** 年度公开时限：读取（登录可见）与保存（后端 require_admin 把关）。 */
export async function GET() {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) {
    return auth.response;
  }
  try {
    const response = await fetch(`${apiBase}/api/disclosure-calendar`, {
      cache: "no-store",
      headers: auth.headers,
    });
    return NextResponse.json(await response.json(), { status: response.status });
  } catch (error) {
    console.error("Failed to load disclosure calendar:", error);
    return NextResponse.json({ error: "公开时限读取失败" }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const response = await fetch(`${apiBase}/api/admin/disclosure-calendar`, {
      method: "POST",
      cache: "no-store",
      headers: await backendAuthHeadersWithSession({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    const text = await response.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Failed to save disclosure calendar:", error);
    return NextResponse.json({ error: "公开时限保存失败" }, { status: 500 });
  }
}
