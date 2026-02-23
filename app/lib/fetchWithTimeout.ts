import { apiTimeout } from "./apiBase";

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {}
) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), apiTimeout);
  const signal = init.signal ?? controller.signal;

  try {
    return await fetch(input, { ...init, signal });
  } finally {
    clearTimeout(timeoutId);
  }
}
