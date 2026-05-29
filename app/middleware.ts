import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME } from "@/lib/session";

const PUBLIC_API_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/me",
  "/api/health",
]);

function buildSecurityHeaders(): Record<string, string> {
  const isDevelopment = process.env.NODE_ENV !== "production";
  const scriptSrc = ["'self'", "'unsafe-inline'"];
  const connectSrc = ["'self'"];

  if (isDevelopment) {
    scriptSrc.push("'unsafe-eval'");
    connectSrc.push(
      "http://localhost:*",
      "http://127.0.0.1:*",
      "ws://localhost:*",
      "ws://127.0.0.1:*"
    );
  }

  return {
    "Content-Security-Policy": [
    "default-src 'self'",
    `script-src ${scriptSrc.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSrc.join(" ")}`,
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; "),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  };
}

function withSecurityHeaders(response: NextResponse): NextResponse {
  for (const [name, value] of Object.entries(buildSecurityHeaders())) {
    response.headers.set(name, value);
  }
  return response;
}

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
  if (pathname === "/favicon.svg") {
    return true;
  }
  return false;
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (isPublicPath(pathname)) {
    return withSecurityHeaders(NextResponse.next());
  }

  const sessionToken = request.cookies.get(SESSION_COOKIE_NAME)?.value?.trim();
  if (sessionToken) {
    return withSecurityHeaders(NextResponse.next());
  }

  if (pathname.startsWith("/api")) {
    return withSecurityHeaders(
      NextResponse.json({ detail: "not logged in" }, { status: 401 })
    );
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  const nextPath = `${pathname}${search}`;
  if (nextPath && nextPath !== "/") {
    loginUrl.searchParams.set("next", nextPath);
  }

  return withSecurityHeaders(NextResponse.redirect(loginUrl));
}

export const config = {
  matcher: ["/:path*"],
};
