import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { revokeLocalSession } from "@/lib/localAuth";
import { clearSessionCookie, readLocalSession, readSessionToken } from "@/lib/localAuthSession";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ success: true });

  try {
    const localSession = await readLocalSession();
    if (localSession) {
      await revokeLocalSession(localSession.token);
      return response;
    }

    const sessionToken = readSessionToken();
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
    clearSessionCookie(response, request);
  }

  return response;
}
