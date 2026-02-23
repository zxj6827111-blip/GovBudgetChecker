// app/lib/apiBase.ts
const rawApiBase =
  process.env.BACKEND_URL ??
  process.env.API_ORIGIN ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8000";

export const apiBase = rawApiBase.replace(/\/+$/, "");

export const apiTimeout = Number(process.env.NEXT_PUBLIC_API_TIMEOUT ?? 20000);
