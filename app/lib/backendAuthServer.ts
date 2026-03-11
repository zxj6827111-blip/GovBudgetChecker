import { cookies } from "next/headers";

import { backendAuthHeaders } from "@/lib/backendAuth";
import { SESSION_COOKIE_NAME } from "@/lib/session";

export function backendAuthHeadersWithSession(extra?: HeadersInit): Headers {
  const headers = backendAuthHeaders(extra);
  const sessionToken = cookies().get(SESSION_COOKIE_NAME)?.value?.trim();
  if (sessionToken) {
    headers.set("X-Session-Token", sessionToken);
  }
  return headers;
}
