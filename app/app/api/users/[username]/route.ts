import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import {
  LocalAuthError,
  deleteLocalUser,
  updateLocalUser,
} from "@/lib/localAuth";
import { readLocalSession, readSessionToken } from "@/lib/localAuthSession";

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

function unauthorizedResponse() {
  return NextResponse.json({ detail: "not logged in" }, { status: 401 });
}

function forbiddenResponse() {
  return NextResponse.json({ detail: "admin privileges required" }, { status: 403 });
}

function parseBooleanField(value: unknown, fieldName: string): boolean {
  if (typeof value !== "boolean") {
    throw new LocalAuthError(400, `${fieldName} must be boolean`);
  }
  return value;
}

function parsePasswordField(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new LocalAuthError(400, "password is required");
  }
  return value;
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: { username: string } }
) {
  const sessionToken = readSessionToken();
  if (!sessionToken) {
    return unauthorizedResponse();
  }

  try {
    const body = parseJsonObject(await request.json().catch(() => null));
    const localSession = await readLocalSession();
    if (localSession) {
      if (!localSession.user.is_admin) {
        return forbiddenResponse();
      }

      const updates: { is_admin?: boolean; is_active?: boolean; password?: string } = {};
      try {
        if ("is_admin" in body) {
          updates.is_admin = parseBooleanField(body.is_admin, "is_admin");
        }
        if ("is_active" in body) {
          updates.is_active = parseBooleanField(body.is_active, "is_active");
        }
        if ("password" in body) {
          updates.password = parsePasswordField(body.password);
        }
        const user = await updateLocalUser(params.username, updates);
        return NextResponse.json(user, { status: 200 });
      } catch (localError) {
        if (localError instanceof LocalAuthError) {
          return NextResponse.json(
            { detail: localError.detail },
            { status: localError.status },
          );
        }
        throw localError;
      }
    }

    const backendResponse = await fetchWithTimeout(
      `${apiBase}/api/users/${encodeURIComponent(params.username)}`,
      {
        method: "PATCH",
        headers: backendAuthHeaders({
          "Content-Type": "application/json",
          "X-Session-Token": sessionToken,
        }),
        body: JSON.stringify(body),
        cache: "no-store",
      }
    );
    const payload = parsePayload(await backendResponse.text());
    return NextResponse.json(payload, { status: backendResponse.status });
  } catch (error) {
    console.error("Users PATCH proxy failed:", error);
    return NextResponse.json({ detail: "failed to update user" }, { status: 500 });
  }
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { username: string } }
) {
  const sessionToken = readSessionToken();
  if (!sessionToken) {
    return unauthorizedResponse();
  }

  try {
    const localSession = await readLocalSession();
    if (localSession) {
      if (!localSession.user.is_admin) {
        return forbiddenResponse();
      }

      try {
        await deleteLocalUser(params.username, localSession.user.username);
        return NextResponse.json({ success: true }, { status: 200 });
      } catch (localError) {
        if (localError instanceof LocalAuthError) {
          return NextResponse.json(
            { detail: localError.detail },
            { status: localError.status },
          );
        }
        throw localError;
      }
    }

    const backendResponse = await fetchWithTimeout(
      `${apiBase}/api/users/${encodeURIComponent(params.username)}`,
      {
        method: "DELETE",
        headers: backendAuthHeaders({
          "Content-Type": "application/json",
          "X-Session-Token": sessionToken,
        }),
        cache: "no-store",
      }
    );
    const payload = parsePayload(await backendResponse.text());
    return NextResponse.json(payload, { status: backendResponse.status });
  } catch (error) {
    console.error("Users DELETE proxy failed:", error);
    return NextResponse.json({ detail: "failed to delete user" }, { status: 500 });
  }
}
