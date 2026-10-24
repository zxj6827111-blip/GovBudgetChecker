import { NextResponse } from "next/server";

import { apiBase } from "@/lib/apiBase";
import { backendAuthHeaders } from "@/lib/backendAuth";
import { fetchWithTimeout } from "@/lib/fetchWithTimeout";
import { readLocalSession, readSessionToken } from "@/lib/localAuthSession";

type AuthenticatedBackendHeaders = {
  ok: true;
  headers: Headers;
  sessionToken: string;
};

type AuthenticationFailure = {
  ok: false;
  response: NextResponse;
};

export type BackendAuthResult =
  | AuthenticatedBackendHeaders
  | AuthenticationFailure;

function authFailure(detail: string, status = 401, headers?: HeadersInit): AuthenticationFailure {
  return {
    ok: false,
    response: NextResponse.json({ detail }, { status, headers }),
  };
}

export const RATE_LIMITED_DETAIL = "当前操作请求过于频繁，后端暂时限流。请稍等一分钟后重试。";

/** 后端限流时会话校验没有失败也没有不可用：原样回 429 和重试时间，不能改报 503 或未登录。 */
export function rateLimitedResponse(retryAfter: string | null): NextResponse {
  return NextResponse.json(
    { detail: RATE_LIMITED_DETAIL },
    { status: 429, headers: retryAfter ? { "Retry-After": retryAfter } : undefined },
  );
}

function buildBackendHeaders(
  sessionToken: string,
  extra?: HeadersInit,
): Headers {
  const headers = backendAuthHeaders(extra);
  headers.set("X-Session-Token", sessionToken);
  return headers;
}

export async function requireBackendAuthHeaders(
  extra?: HeadersInit,
): Promise<BackendAuthResult> {
  const sessionToken = await readSessionToken();
  if (!sessionToken) {
    return authFailure("not logged in");
  }

  let localSession;
  try {
    localSession = await readLocalSession();
  } catch {
    return authFailure("session validation unavailable", 503);
  }
  if (localSession) {
    return {
      ok: true,
      headers: buildBackendHeaders(sessionToken, extra),
      sessionToken,
    };
  }

  try {
    const response = await fetchWithTimeout(
      `${apiBase}/api/auth/me`,
      {
        cache: "no-store",
        headers: buildBackendHeaders(sessionToken, {
          "Content-Type": "application/json",
        }),
      },
      5000,
    );

    if (response.status === 401 || response.status === 403) {
      return authFailure("not logged in");
    }
    if (response.status === 429) {
      return { ok: false, response: rateLimitedResponse(response.headers.get("Retry-After")) };
    }
    if (!response.ok) {
      return authFailure("session validation unavailable", 503);
    }
  } catch {
    return authFailure("session validation unavailable", 503);
  }

  return {
    ok: true,
    headers: buildBackendHeaders(sessionToken, extra),
    sessionToken,
  };
}
