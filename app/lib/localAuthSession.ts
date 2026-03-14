import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import {
  getLocalUserByToken,
  isLocalAuthFallbackEnabled,
  type LocalAuthUser,
} from "@/lib/localAuth";
import {
  SESSION_COOKIE_NAME,
  SESSION_MAX_AGE_SECONDS,
  shouldUseSecureSessionCookie,
} from "@/lib/session";

export type LocalSession = {
  token: string;
  user: LocalAuthUser;
};

export function readSessionToken(): string {
  return cookies().get(SESSION_COOKIE_NAME)?.value?.trim() ?? "";
}

export async function readLocalSession(): Promise<LocalSession | null> {
  if (!isLocalAuthFallbackEnabled()) {
    return null;
  }

  const token = readSessionToken();
  if (!token) {
    return null;
  }

  const user = await getLocalUserByToken(token);
  if (!user) {
    return null;
  }

  return { token, user };
}

export function setSessionCookie(
  response: NextResponse,
  request: NextRequest,
  token: string,
): void {
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: token,
    httpOnly: true,
    secure: shouldUseSecureSessionCookie(request),
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
}

export function clearSessionCookie(
  response: NextResponse,
  request: NextRequest,
): void {
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: "",
    httpOnly: true,
    secure: shouldUseSecureSessionCookie(request),
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
}
