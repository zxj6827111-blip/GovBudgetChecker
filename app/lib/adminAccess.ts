import "server-only";

import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { readLocalSession, readSessionToken } from "@/lib/localAuthSession";
import { rateLimitedResponse } from "@/lib/routeAuth";

type AdminAccessSuccess = {
  ok: true;
  actor: string;
  isAdmin: boolean;
};

type AdminAccessFailure = {
  ok: false;
  response: NextResponse;
};

export type AdminAccessResult = AdminAccessSuccess | AdminAccessFailure;

function failure(detail: string, status: number): AdminAccessFailure {
  return {
    ok: false,
    response: NextResponse.json({ detail }, { status }),
  };
}

class BackendRateLimited extends Error {
  constructor(readonly retryAfter: string | null) {
    super("backend rate limited");
  }
}

async function readBackendUser(sessionToken: string): Promise<{ username?: string; is_admin?: boolean } | null> {
  const response = await fetchWithTimeout(
    `${apiBase}/api/auth/me`,
    {
      cache: "no-store",
      headers: backendAuthHeaders({
        "Content-Type": "application/json",
        "X-Session-Token": sessionToken,
      }),
    },
    5000,
  );
  if (response.status === 429) {
    // 限流不等于会话失效：报未登录会让前端把用户踢回登录页。
    throw new BackendRateLimited(response.headers.get("Retry-After"));
  }
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json().catch(() => ({}))) as {
    user?: { username?: string; is_admin?: boolean } | null;
  };
  return payload.user ?? null;
}

export async function requireAdminAccess(options?: { adminOnly?: boolean }): Promise<AdminAccessResult> {
  const adminOnly = options?.adminOnly ?? true;
  const sessionToken = await readSessionToken();
  if (!sessionToken) {
    return failure("not logged in", 401);
  }

  const localSession = await readLocalSession();
  if (localSession) {
    if (adminOnly && !localSession.user.is_admin) {
      return failure("admin privileges required", 403);
    }
    return {
      ok: true,
      actor: localSession.user.username,
      isAdmin: Boolean(localSession.user.is_admin),
    };
  }

  try {
    const backendUser = await readBackendUser(sessionToken);
    if (!backendUser) {
      return failure("not logged in", 401);
    }
    const isAdmin = Boolean(backendUser.is_admin);
    if (adminOnly && !isAdmin) {
      return failure("admin privileges required", 403);
    }
    return {
      ok: true,
      actor: backendUser.username || "backend-user",
      isAdmin,
    };
  } catch (error) {
    if (error instanceof BackendRateLimited) {
      return { ok: false, response: rateLimitedResponse(error.retryAfter) };
    }
    return failure("session validation unavailable", 503);
  }
}
