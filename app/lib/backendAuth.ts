export function backendAuthHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra ?? {});
  const devFallbackKey =
    process.env.NODE_ENV === "development" ? "dev-local-key" : "";
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    process.env.BACKEND_API_KEY ||
    devFallbackKey;

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  return headers;
}
