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
  // 密钥解析顺序：显式 env → 根 .env（与后端同源）。不再保留
  // "change_me_to_a_strong_secret" 开发兜底——该兜底与后端实际 key 不一致
  // 时请求会静默 403，掩盖配置问题（GPT5.6 P1-5：统一 fail-closed，
  // 缺 key 让错误显式暴露而不是猜一个错值）。
  const apiKey =
    process.env.BACKEND_API_KEY ||
    process.env.GOVBUDGET_API_KEY ||
    readRootEnvApiKey();

  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  return headers;
}
