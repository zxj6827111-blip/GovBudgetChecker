import { normalizeNextPath } from "./loginNextPath";
/** 客户账号默认进入主管端；仍沿用原有站内路径安全校验。 */
export function supervisorLoginPath(path: string | null, user: unknown): string {
  const normalized = normalizeNextPath(path);
  const readOnly = user && typeof user === "object" && "is_read_only" in user && user.is_read_only === true;
  return readOnly && !(normalized === "/supervision" || normalized.startsWith("/supervision/") || normalized.startsWith("/supervision?")) ? "/supervision" : normalized;
}
