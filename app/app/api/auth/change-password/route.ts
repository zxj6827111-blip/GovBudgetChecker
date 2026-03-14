import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { LocalAuthError, changeLocalPassword } from "@/lib/localAuth";
import { clearSessionCookie, readLocalSession, readSessionToken } from "@/lib/localAuthSession";

export const dynamic = "force-dynamic";

function parseJsonObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function parsePayload(text: string): Record<string, unknown> {
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { detail: text || "invalid backend response" };
  }
}

export async function POST(request: NextRequest) {
  const sessionToken = readSessionToken();
  if (!sessionToken) {
    return NextResponse.json({ detail: "not logged in" }, { status: 401 });
  }

  const body = parseJsonObject(await request.json().catch(() => null));
  const oldPassword =
    typeof body?.old_password === "string" ? body.old_password : "";
  const newPassword =
    typeof body?.new_password === "string" ? body.new_password : "";
  if (!oldPassword || !newPassword) {
    return NextResponse.json(
      { detail: "old_password and new_password are required" },
      { status: 400 },
    );
  }

  try {
    const localSession = await readLocalSession();
    if (localSession) {
      try {
        await changeLocalPassword(localSession.token, oldPassword, newPassword);
      } catch (localError) {
        if (localError instanceof LocalAuthError) {
          return NextResponse.json(
            { detail: localError.detail },
            { status: localError.status },
          );
        }
        throw localError;
      }

      const response = NextResponse.json(
        { success: true, message: "password updated, please login again" },
        { status: 200 },
      );
      clearSessionCookie(response, request);
      return response;
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
    if (backendResponse.ok) {
      clearSessionCookie(response, request);
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
