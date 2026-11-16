import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { requireBackendAuthHeaders } from "@/lib/routeAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";


async function forwardMaterialReports(
  request: NextRequest,
  path: string,
  init: RequestInit & { headers: HeadersInit },
): Promise<NextResponse> {
  const url = new URL(`${apiBase}${path}`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  try {
    const response = await fetchWithTimeout(url.toString(), { ...init, cache: "no-store" });
    const text = await response.text();
    let payload: unknown = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { detail: text };
      }
    }
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "材料报告服务暂时不可用" }, { status: 502 });
  }
}

export async function proxyMaterialReports(request: NextRequest, path: string) {
  const auth = await requireBackendAuthHeaders();
  if (!auth.ok) return auth.response;
  return forwardMaterialReports(request, path, { headers: auth.headers });
}

export async function proxyMaterialReportsMutation(
  request: NextRequest,
  path: string,
  method: "DELETE" | "POST",
) {
  const auth = await requireBackendAuthHeaders(
    method === "POST" ? { "Content-Type": "application/json" } : undefined,
  );
  if (!auth.ok) return auth.response;
  const body = method === "POST" ? await request.text() : undefined;
  return forwardMaterialReports(request, path, {
    method,
    headers: auth.headers,
    ...(body !== undefined ? { body } : {}),
  });
}

