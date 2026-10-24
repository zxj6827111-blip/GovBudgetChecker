import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";


export async function proxyMaterialReports(request: NextRequest, path: string) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  const url = new URL(`${apiBase}${path}`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  try {
    const response = await fetchWithTimeout(url.toString(), { cache: "no-store", headers: auth.headers });
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json({ detail: "材料报告服务暂时不可用" }, { status: 502 });
  }
}

