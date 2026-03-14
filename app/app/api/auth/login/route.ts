import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { LocalAuthError, loginLocalUser, isLocalAuthFallbackEnabled } from "@/lib/localAuth";
import { setSessionCookie } from "@/lib/localAuthSession";

type LoginPayload = {
  token?: string;
  user?: Record<string, unknown>;
  detail?: string;
};

function parseJsonObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function parseBackendPayload(text: string): LoginPayload {
  try {
    return JSON.parse(text) as LoginPayload;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function POST(request: NextRequest) {
  const body = parseJsonObject(await request.json().catch(() => null));
  const username = String(body?.username ?? "").trim();
  const password = typeof body?.password === "string" ? body.password : "";
  if (!username) {
    return NextResponse.json({ detail: "username is required" }, { status: 400 });
  }
  if (!password) {
    return NextResponse.json({ detail: "password is required" }, { status: 400 });
  }

  try {
    const backendResponse = await fetchWithTimeout(`${apiBase}/api/auth/login`, {
      method: "POST",
      headers: backendAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ username, password }),
      cache: "no-store",
    });
    const payload = parseBackendPayload(await backendResponse.text());

    if (!backendResponse.ok) {
      return NextResponse.json(payload, { status: backendResponse.status });
    }

    const token = String(payload.token ?? "").trim();
    if (!token) {
      return NextResponse.json(
        { detail: "login succeeded but token is missing" },
        { status: 502 },
      );
    }

    const response = NextResponse.json({ user: payload.user ?? null }, { status: 200 });
    setSessionCookie(response, request, token);
    return response;
  } catch (error) {
    if (!isLocalAuthFallbackEnabled()) {
      console.error("Login proxy failed:", error);
      return NextResponse.json({ detail: "login failed" }, { status: 500 });
    }

    try {
      const { token, user } = await loginLocalUser(username, password);
      const response = NextResponse.json({ user }, { status: 200 });
      setSessionCookie(response, request, token);
      return response;
    } catch (localError) {
      if (localError instanceof LocalAuthError) {
        return NextResponse.json(
          { detail: localError.detail },
          { status: localError.status },
        );
      }
      console.error("Local login fallback failed:", localError);
      return NextResponse.json({ detail: "login failed" }, { status: 500 });
    }
  }
}
