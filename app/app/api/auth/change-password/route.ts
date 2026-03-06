import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { SESSION_COOKIE_NAME, shouldUseSecureSessionCookie } from "@/lib/session";

export const dynamic = "force-dynamic";

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function POST(request: NextRequest) {
  try {
    const sessionToken = cookies().get(SESSION_COOKIE_NAME)?.value?.trim();
    if (!sessionToken) {
      return NextResponse.json({ detail: "not logged in" }, { status: 401 });
    }

    const body = await request.json().catch(() => ({}));
    const oldPassword =
      typeof body?.old_password === "string" ? body.old_password : "";
    const newPassword =
      typeof body?.new_password === "string" ? body.new_password : "";
    if (!oldPassword || !newPassword) {
      return NextResponse.json(
        { detail: "old_password and new_password are required" },
        { status: 400 }
      );
    }

    const backendResponse = await fetchWithTimeout(
      `${apiBase}/api/auth/change-password`,
      {
        method: "POST",
        headers: backendAuthHeaders({
          "Content-Type": "application/json",
          "X-Session-Token": sessionToken,
        }),
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
        cache: "no-store",
      }
    );
    const payload = parsePayload(await backendResponse.text());
    const response = NextResponse.json(payload, { status: backendResponse.status });
    const secureCookie = shouldUseSecureSessionCookie(request);
    if (backendResponse.ok) {
      response.cookies.set({
        name: SESSION_COOKIE_NAME,
        value: "",
        httpOnly: true,
        secure: secureCookie,
        sameSite: "lax",
        path: "/",
        maxAge: 0,
      });
    }
    return response;
  } catch (error) {
    console.error("Change password proxy failed:", error);
    return NextResponse.json(
      { detail: "failed to change password" },
      { status: 500 }
    );
  }
}
