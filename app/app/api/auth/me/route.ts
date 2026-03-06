import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { SESSION_COOKIE_NAME } from "@/lib/session";

export const dynamic = "force-dynamic";

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function GET() {
  try {
    const sessionToken = cookies().get(SESSION_COOKIE_NAME)?.value?.trim();
    if (!sessionToken) {
      return NextResponse.json({ detail: "not logged in" }, { status: 401 });
    }

    const backendResponse = await fetchWithTimeout(`${apiBase}/api/auth/me`, {
      headers: backendAuthHeaders({
        "Content-Type": "application/json",
        "X-Session-Token": sessionToken,
      }),
      cache: "no-store",
    });
    const payload = parsePayload(await backendResponse.text());

    return NextResponse.json(payload, { status: backendResponse.status });
  } catch (error) {
    console.error("Auth /me proxy failed:", error);
    return NextResponse.json({ detail: "failed to fetch user" }, { status: 500 });
  }
}
