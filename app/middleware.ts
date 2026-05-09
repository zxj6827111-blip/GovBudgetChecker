import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME } from "@/lib/session";

const PUBLIC_API_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/me",
  "/api/health",
]);

function isE2EPathEnabled(): boolean {
  return (
    process.env.NODE_ENV !== "production" ||
    process.env.GBC_ENABLE_E2E_PAGES === "true"
  );
}

function isPublicPath(pathname: string): boolean {
  if (pathname === "/login") {
    return true;
  }
  if (pathname.startsWith("/_next")) {
    return true;
  }
  if (PUBLIC_API_PATHS.has(pathname)) {
    return true;
  }
  if (pathname.startsWith("/e2e") && isE2EPathEnabled()) {
    return true;
  }
  if (pathname === "/favicon.ico") {
    return true;
  }
  return false;
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const sessionToken = request.cookies.get(SESSION_COOKIE_NAME)?.value?.trim();
  if (sessionToken) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/api")) {
    return NextResponse.json({ detail: "not logged in" }, { status: 401 });
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  const nextPath = `${pathname}${search}`;
  if (nextPath && nextPath !== "/") {
    loginUrl.searchParams.set("next", nextPath);
  }

  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/:path*"],
};
