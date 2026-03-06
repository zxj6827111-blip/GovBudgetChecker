export const SESSION_COOKIE_NAME = "gbc_session";

export const SESSION_MAX_AGE_SECONDS = Number(
  process.env.GBC_SESSION_MAX_AGE_SECONDS ?? 8 * 60 * 60
);
