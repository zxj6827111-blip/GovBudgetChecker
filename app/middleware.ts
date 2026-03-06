import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME } from "@/lib/session";

function isPublicPath(pathname: string): boolean {
  if (pathname === "/login") {
    return true;
  }
  if (pathname.startsWith("/_next")) {
    return true;
  }
  if (pathname.startsWith("/api")) {
    return true;
  }
  if (pathname.startsWith("/e2e")) {
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
