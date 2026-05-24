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

function authFailure(detail: string, status = 401): AuthenticationFailure {
  return {
    ok: false,
    response: NextResponse.json({ detail }, { status }),
  };
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
