import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { SESSION_COOKIE_NAME } from "@/lib/session";

export const dynamic = "force-dynamic";

function readSessionToken(): string {
  return cookies().get(SESSION_COOKIE_NAME)?.value?.trim() ?? "";
}

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

function unauthorizedResponse() {
  return NextResponse.json({ detail: "not logged in" }, { status: 401 });
}

export async function GET() {
  try {
    const sessionToken = readSessionToken();
    if (!sessionToken) {
      return unauthorizedResponse();
    }

    const backendResponse = await fetchWithTimeout(`${apiBase}/api/users`, {
      headers: backendAuthHeaders({
        "Content-Type": "application/json",
        "X-Session-Token": sessionToken,
      }),
      cache: "no-store",
    });
    const payload = parsePayload(await backendResponse.text());
    return NextResponse.json(payload, { status: backendResponse.status });
  } catch (error) {
    console.error("Users GET proxy failed:", error);
    return NextResponse.json({ detail: "failed to fetch users" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const sessionToken = readSessionToken();
    if (!sessionToken) {
      return unauthorizedResponse();
    }

    const body = await request.json().catch(() => ({}));
    const backendResponse = await fetchWithTimeout(`${apiBase}/api/users`, {
      method: "POST",
      headers: backendAuthHeaders({
        "Content-Type": "application/json",
        "X-Session-Token": sessionToken,
      }),
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const payload = parsePayload(await backendResponse.text());
    return NextResponse.json(payload, { status: backendResponse.status });
  } catch (error) {
    console.error("Users POST proxy failed:", error);
    return NextResponse.json({ detail: "failed to create user" }, { status: 500 });
  }
}
