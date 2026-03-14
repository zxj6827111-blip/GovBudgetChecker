export function backendAuthHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra ?? {});
  const devFallbackKey =
    process.env.NODE_ENV !== "production" ? "change_me_to_a_strong_secret" : "";
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    process.env.BACKEND_API_KEY ||
    devFallbackKey;

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  return headers;
}
