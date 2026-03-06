export const SESSION_COOKIE_NAME = "gbc_session";

export const SESSION_MAX_AGE_SECONDS = Number(
  process.env.GBC_SESSION_MAX_AGE_SECONDS ?? 8 * 60 * 60
);

function parseBoolean(raw: string | undefined): boolean | null {
  if (!raw) {
    return null;
  }

  const value = raw.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(value)) {
    return true;
  }
  if (["0", "false", "no", "off"].includes(value)) {
    return false;
  }
  return null;
}

export function shouldUseSecureSessionCookie(request?: {
  headers?: Headers;
  nextUrl?: { protocol?: string };
}): boolean {
  const envOverride = parseBoolean(process.env.GBC_SECURE_COOKIES);
  if (envOverride !== null) {
    return envOverride;
  }

  const forwardedProto = request?.headers
    ?.get("x-forwarded-proto")
    ?.split(",")[0]
    ?.trim()
    ?.toLowerCase();
  if (forwardedProto) {
    return forwardedProto === "https";
  }

  const protocol = request?.nextUrl?.protocol?.toLowerCase();
  return protocol === "https:";
}
