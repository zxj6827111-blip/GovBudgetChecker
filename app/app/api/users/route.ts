import { NextRequest, NextResponse } from "next/server";
import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import {
  LocalAuthError,
  createLocalUser,
  listLocalUsers,
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

function parseBooleanField(
  value: unknown,
  fieldName: string,
): boolean {
  if (typeof value !== "boolean") {
    throw new LocalAuthError(400, `${fieldName} must be boolean`);
  }
  return value;
}

export async function GET() {
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
      return NextResponse.json({ users: await listLocalUsers() }, { status: 200 });
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

      try {
        const user = await createLocalUser({
          username: String(body?.username ?? ""),
          password: typeof body?.password === "string" ? body.password : "",
          is_admin:
            "is_admin" in body ? parseBooleanField(body.is_admin, "is_admin") : false,
        });
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
