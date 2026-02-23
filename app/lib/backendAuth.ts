export function backendAuthHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra ?? {});
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    process.env.BACKEND_API_KEY ||
    "";

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  return headers;
}

