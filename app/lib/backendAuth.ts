import "server-only";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

let cachedRootApiKey: string | null | undefined;

function readRootEnvApiKey(): string {
  if (cachedRootApiKey !== undefined) {
    return cachedRootApiKey ?? "";
  }

  cachedRootApiKey = null;
  const candidates = [resolve(process.cwd(), "..", ".env"), resolve(process.cwd(), ".env")];

  for (const filePath of candidates) {
    try {
      const content = readFileSync(filePath, "utf8");
      const match = content.match(/^GOVBUDGET_API_KEY=(.+)$/m);
      const value = match?.[1]?.trim();
      if (value) {
        cachedRootApiKey = value.replace(/^["']|["']$/g, "");
        break;
      }
    } catch {
      // In hosted environments the key should come from process.env.
    }
  }

  return cachedRootApiKey ?? "";
}

export function backendAuthHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra ?? {});
  const devFallbackKey =
    process.env.NODE_ENV !== "production" ? "change_me_to_a_strong_secret" : "";
  const apiKey =
    process.env.GOVBUDGET_API_KEY ||
    readRootEnvApiKey() ||
    process.env.BACKEND_API_KEY ||
    devFallbackKey;

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  return headers;
}
