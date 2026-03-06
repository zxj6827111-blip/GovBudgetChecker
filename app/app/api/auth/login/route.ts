import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS } from "@/lib/session";

type LoginPayload = {
  token?: string;
  user?: Record<string, unknown>;
  detail?: string;
};

function parseBackendPayload(text: string): LoginPayload {
  try {
    return JSON.parse(text) as LoginPayload;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const username = String(body?.username ?? "").trim();
    const password = typeof body?.password === "string" ? body.password : "";
    if (!username) {
      return NextResponse.json(
        { detail: "username is required" },
        { status: 400 }
      );
    }
    if (!password) {
      return NextResponse.json(
        { detail: "password is required" },
        { status: 400 }
      );
    }

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
        { status: 502 }
      );
    }

    const response = NextResponse.json(
      { user: payload.user ?? null },
      { status: 200 }
    );
    response.cookies.set({
      name: SESSION_COOKIE_NAME,
      value: token,
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: SESSION_MAX_AGE_SECONDS,
    });
    return response;
  } catch (error) {
    console.error("Login proxy failed:", error);
    return NextResponse.json({ detail: "login failed" }, { status: 500 });
  }
}
