import { NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { readLocalSession, readSessionToken } from "@/lib/localAuthSession";

export const dynamic = "force-dynamic";

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function GET(request: Request) {
  const sessionToken = readSessionToken();
  if (!sessionToken) {
    if (request.headers.get("x-login-probe") === "1") {
      return NextResponse.json({ user: null }, { status: 200 });
    }
    return NextResponse.json({ detail: "not logged in" }, { status: 401 });
  }

  try {
    const localSession = await readLocalSession();
    if (localSession) {
      return NextResponse.json({ user: localSession.user }, { status: 200 });
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
