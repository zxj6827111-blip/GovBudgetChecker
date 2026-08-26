export const FALLBACK_REDIRECT_STATUS = 307;

export function shouldFallbackToLocal(status: number): boolean {
  return status === 429 || status >= 500;
}