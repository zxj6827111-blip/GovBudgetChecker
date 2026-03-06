import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { SESSION_COOKIE_NAME, shouldUseSecureSessionCookie } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ success: true });
  const secureCookie = shouldUseSecureSessionCookie(request);

  try {
    const sessionToken = cookies().get(SESSION_COOKIE_NAME)?.value?.trim();
    if (sessionToken) {
      try {
        await fetchWithTimeout(`${apiBase}/api/auth/logout`, {
          method: "POST",
          headers: backendAuthHeaders({
            "Content-Type": "application/json",
            "X-Session-Token": sessionToken,
          }),
          cache: "no-store",
        });
      } catch (error) {
        console.error("Auth logout backend call failed:", error);
      }
    }
  } finally {
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
}
