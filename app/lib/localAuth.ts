import { createHmac, pbkdf2Sync, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

type StoredUser = {
  username: string;
  password_hash: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: number;
  updated_at: number;
  session_version: number;
};

export type LocalAuthUser = {
  username: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: number;
  updated_at: number;
};

type StoredUsersFile = {
  users?: unknown;
  updated_at?: unknown;
};

type SessionPayload = {
  username: string;
  issuedAt: number;
  expiresAt: number;
  sessionVersion: number;
};

export class LocalAuthError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

const HASH_ALGO = "pbkdf2_sha256";
const SESSION_TOKEN_PREFIX = "gbcs1";
const USERS_FILE = resolve(process.cwd(), "..", "data", "users.json");
const PASSWORD_ITERATIONS = Number(process.env.USER_PASSWORD_ITERATIONS ?? 260000);
const PASSWORD_MIN_LENGTH = Number(process.env.USER_PASSWORD_MIN_LENGTH ?? 6);
const SESSION_TTL_SECONDS = Number(process.env.GBC_SESSION_MAX_AGE_SECONDS ?? 8 * 60 * 60);
const DEFAULT_ADMIN_USERNAME = process.env.DEFAULT_ADMIN_USERNAME?.trim() || "admin";
const DEFAULT_ADMIN_PASSWORD = process.env.DEFAULT_ADMIN_PASSWORD?.trim() || "admin123";
const DEFAULT_LEGACY_PASSWORD = process.env.DEFAULT_LEGACY_PASSWORD?.trim() || "change_me_123";

let fileLock: Promise<unknown> = Promise.resolve();

function parseBoolean(value: string | undefined): boolean | null {
  if (!value) return null;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return null;
}

export function isLocalAuthFallbackEnabled(): boolean {
  const envOverride = parseBoolean(process.env.GBC_ENABLE_LOCAL_AUTH_FALLBACK);
  if (envOverride !== null) {
    return envOverride;
  }
  return process.env.NODE_ENV !== "production";
}

function withFileLock<T>(fn: () => Promise<T>): Promise<T> {
  const next = fileLock.then(fn, fn);
  fileLock = next.then(
    () => undefined,
    () => undefined,
  );
  return next;
}

function normalizeUsername(username: string): string {
  return username.trim().toLowerCase();
}

function validateUsername(username: string): string {
  const text = String(username || "").trim();
  if (!text) {
    throw new LocalAuthError(400, "username is required");
  }
  if (text.length > 64) {
    throw new LocalAuthError(400, "username is too long");
  }
  if (/\s/.test(text)) {
    throw new LocalAuthError(400, "username cannot contain spaces");
  }
  return text;
}

function requirePassword(password: string): string {
  const text = String(password || "");
  if (!text) {
    throw new LocalAuthError(400, "password is required");
  }
  if (text.length > 256) {
    throw new LocalAuthError(400, "password is too long");
  }
  return text;
}

function validateNewPassword(password: string): string {
  const text = requirePassword(password);
  if (text.length < PASSWORD_MIN_LENGTH) {
    throw new LocalAuthError(400, `password is too short (min ${PASSWORD_MIN_LENGTH} chars)`);
  }
  return text;
}

function b64urlEncode(raw: Buffer): string {
  return raw.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecode(text: string): Buffer {
  const normalized = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  return Buffer.from(padded, "base64");
}

function publicUser(user: StoredUser): LocalAuthUser {
  return {
    username: user.username,
    is_admin: Boolean(user.is_admin),
    is_active: Boolean(user.is_active),
    created_at: Number(user.created_at || 0),
    updated_at: Number(user.updated_at || 0),
  };
}

function isPasswordHash(value: string): boolean {
  const parts = value.split("$", 4);
  return parts.length === 4 && parts[0] === HASH_ALGO;
}

function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const digest = pbkdf2Sync(password, salt, PASSWORD_ITERATIONS, 32, "sha256");
  return `${HASH_ALGO}$${PASSWORD_ITERATIONS}$${b64urlEncode(salt)}$${b64urlEncode(digest)}`;
}

function verifyPassword(password: string, passwordHash: string): boolean {
  if (!isPasswordHash(passwordHash)) {
    return false;
  }

  const [algo, iterationsText, saltText, digestText] = passwordHash.split("$", 4);
  if (algo !== HASH_ALGO) {
    return false;
  }

  try {
    const iterations = Number(iterationsText);
    const salt = b64urlDecode(saltText);
    const expected = b64urlDecode(digestText);
    const candidate = pbkdf2Sync(password, salt, iterations, expected.length, "sha256");
    return expected.length === candidate.length && timingSafeEqual(candidate, expected);
  } catch {
    return false;
  }
}

function getSessionSecret(): Buffer {
  const raw =
    process.env.USER_SESSION_SECRET?.trim() ||
    process.env.GOVBUDGET_API_KEY?.trim() ||
    (process.env.NODE_ENV === "test" ? "test-user-session-secret" : "dev-user-session-secret");
  return Buffer.from(raw, "utf-8");
}

function signSessionBody(bodyText: string): string {
  return b64urlEncode(createHmac("sha256", getSessionSecret()).update(bodyText, "ascii").digest());
}

function buildSessionToken(user: StoredUser): string {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    u: user.username,
    iat: now,
    exp: now + SESSION_TTL_SECONDS,
    sv: Math.max(Number(user.session_version || 0), 0),
  };
  const bodyText = b64urlEncode(Buffer.from(JSON.stringify(payload), "utf-8"));
  return `${SESSION_TOKEN_PREFIX}.${bodyText}.${signSessionBody(bodyText)}`;
}

function parseSessionToken(token: string, allowExpired = false): SessionPayload | null {
  const text = String(token || "").trim();
  if (!text) {
    return null;
  }

  const parts = text.split(".");
  if (parts.length !== 3 || parts[0] !== SESSION_TOKEN_PREFIX) {
    return null;
  }

  const [, bodyText, signatureText] = parts;
  const expectedSignature = signSessionBody(bodyText);
  const actualBuffer = Buffer.from(signatureText, "utf-8");
  const expectedBuffer = Buffer.from(expectedSignature, "utf-8");
  if (actualBuffer.length !== expectedBuffer.length || !timingSafeEqual(actualBuffer, expectedBuffer)) {
    return null;
  }

  try {
    const payload = JSON.parse(b64urlDecode(bodyText).toString("utf-8")) as Record<string, unknown>;
    const username = String(payload.u ?? "").trim();
    const issuedAt = Number(payload.iat ?? 0);
    const expiresAt = Number(payload.exp ?? 0);
    const sessionVersion = Math.max(Number(payload.sv ?? 0), 0);

    if (!username || issuedAt <= 0 || expiresAt <= 0 || expiresAt < issuedAt) {
      return null;
    }
    if (!allowExpired && expiresAt <= Math.floor(Date.now() / 1000)) {
      return null;
    }

    return { username, issuedAt, expiresAt, sessionVersion };
  } catch {
    return null;
  }
}

async function ensureUsersDir(): Promise<void> {
  await mkdir(dirname(USERS_FILE), { recursive: true });
}

function resolveLoadedPasswordHash(canonicalUsername: string, rawPasswordHash: string, legacyPassword: string) {
  if (rawPasswordHash && isPasswordHash(rawPasswordHash)) {
    return { passwordHash: rawPasswordHash, migrated: false };
  }
  if (legacyPassword) {
    return { passwordHash: hashPassword(legacyPassword), migrated: true };
  }
  if (canonicalUsername === normalizeUsername(DEFAULT_ADMIN_USERNAME)) {
    return { passwordHash: hashPassword(DEFAULT_ADMIN_PASSWORD), migrated: true };
  }
  return { passwordHash: hashPassword(DEFAULT_LEGACY_PASSWORD), migrated: true };
}

function ensureDefaultAdmin(users: Record<string, StoredUser>): boolean {
  const canonical = normalizeUsername(DEFAULT_ADMIN_USERNAME);
  const now = Date.now() / 1000;
  const existing = users[canonical];

  if (!existing) {
    users[canonical] = {
      username: DEFAULT_ADMIN_USERNAME,
      password_hash: hashPassword(DEFAULT_ADMIN_PASSWORD),
      is_admin: true,
      is_active: true,
      created_at: now,
      updated_at: now,
      session_version: 0,
    };
    return true;
  }

  let changed = false;
  if (!existing.is_admin) {
    existing.is_admin = true;
    changed = true;
  }
  if (!existing.is_active) {
    existing.is_active = true;
    changed = true;
  }
  if (!isPasswordHash(existing.password_hash)) {
    existing.password_hash = hashPassword(DEFAULT_ADMIN_PASSWORD);
    changed = true;
  }
  if (!Number.isFinite(existing.session_version)) {
    existing.session_version = 0;
    changed = true;
  }
  if (changed) {
    existing.updated_at = now;
  }
  return changed;
}

async function saveUsersUnlocked(users: Record<string, StoredUser>): Promise<void> {
  await ensureUsersDir();
  const payload = {
    users: Object.values(users)
      .sort((left, right) => left.username.localeCompare(right.username, "en"))
      .map((user) => ({
        username: user.username,
        password_hash: user.password_hash,
        is_admin: Boolean(user.is_admin),
        is_active: Boolean(user.is_active),
        created_at: user.created_at,
        updated_at: user.updated_at,
        session_version: Math.max(Number(user.session_version || 0), 0),
      })),
    updated_at: Date.now() / 1000,
  };
  const tempPath = `${USERS_FILE}.tmp`;
  await writeFile(tempPath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
  await rename(tempPath, USERS_FILE);
}

async function loadUsersUnlocked(): Promise<Record<string, StoredUser>> {
  await ensureUsersDir();
  let payload: StoredUsersFile = {};
  try {
    const raw = await readFile(USERS_FILE, "utf-8");
    payload = JSON.parse(raw) as StoredUsersFile;
  } catch {
    payload = {};
  }

  const now = Date.now() / 1000;
  const users: Record<string, StoredUser> = {};
  let changed = false;
  const rows = Array.isArray(payload.users) ? payload.users : [];
  for (const row of rows) {
    if (!row || typeof row !== "object") {
      continue;
    }
    const username = String((row as Record<string, unknown>).username ?? "").trim();
    const canonical = normalizeUsername(username);
    if (!canonical) {
      continue;
    }
    const createdAt = Number((row as Record<string, unknown>).created_at ?? now);
    const updatedAt = Number((row as Record<string, unknown>).updated_at ?? createdAt);
    const rawPasswordHash = String((row as Record<string, unknown>).password_hash ?? "").trim();
    const legacyPassword = String((row as Record<string, unknown>).password ?? "");
    const { passwordHash, migrated } = resolveLoadedPasswordHash(canonical, rawPasswordHash, legacyPassword);
    if (migrated) {
      changed = true;
    }
    const sessionVersion = Math.max(Number((row as Record<string, unknown>).session_version ?? 0), 0);
    if (!("session_version" in (row as Record<string, unknown>))) {
      changed = true;
    }

    users[canonical] = {
      username,
      password_hash: passwordHash,
      is_admin: Boolean((row as Record<string, unknown>).is_admin),
      is_active: "is_active" in (row as Record<string, unknown>) ? Boolean((row as Record<string, unknown>).is_active) : true,
      created_at: Number.isFinite(createdAt) ? createdAt : now,
      updated_at: Number.isFinite(updatedAt) ? updatedAt : now,
      session_version: Number.isFinite(sessionVersion) ? sessionVersion : 0,
    };
  }

  if (ensureDefaultAdmin(users)) {
    changed = true;
  }
  if (changed) {
    await saveUsersUnlocked(users);
  }
  return users;
}

function activeAdminCount(users: Record<string, StoredUser>): number {
  return Object.values(users).filter((user) => user.is_admin && user.is_active).length;
}

function bumpSessionVersion(user: StoredUser): void {
  user.session_version = Math.max(Number(user.session_version || 0), 0) + 1;
}

export async function getLocalUserByToken(token: string): Promise<LocalAuthUser | null> {
  return withFileLock(async () => {
    const session = parseSessionToken(token);
    if (!session) {
      return null;
    }
    const users = await loadUsersUnlocked();
    const user = users[normalizeUsername(session.username)];
    if (!user || !user.is_active) {
      return null;
    }
    if (Math.max(Number(user.session_version || 0), 0) !== session.sessionVersion) {
      return null;
    }
    return publicUser(user);
  });
}

export async function loginLocalUser(username: string, password: string): Promise<{ token: string; user: LocalAuthUser }> {
  return withFileLock(async () => {
    const cleanUsername = validateUsername(username);
    const cleanPassword = requirePassword(password);
    const users = await loadUsersUnlocked();
    const user = users[normalizeUsername(cleanUsername)];
    if (!user) {
      throw new LocalAuthError(401, "username not found");
    }
    if (!user.is_active) {
      throw new LocalAuthError(403, "user is disabled");
    }
    if (!verifyPassword(cleanPassword, user.password_hash)) {
      throw new LocalAuthError(401, "invalid username or password");
    }
    return { token: buildSessionToken(user), user: publicUser(user) };
  });
}

export async function revokeLocalSession(token: string): Promise<void> {
  await withFileLock(async () => {
    const session = parseSessionToken(token, true);
    if (!session) {
      return;
    }
    const users = await loadUsersUnlocked();
    const user = users[normalizeUsername(session.username)];
    if (!user) {
      return;
    }
    if (Math.max(Number(user.session_version || 0), 0) !== session.sessionVersion) {
      return;
    }
    bumpSessionVersion(user);
    user.updated_at = Date.now() / 1000;
    await saveUsersUnlocked(users);
  });
}

export async function changeLocalPassword(token: string, oldPassword: string, newPassword: string): Promise<void> {
  await withFileLock(async () => {
    const session = parseSessionToken(token);
    if (!session) {
      throw new LocalAuthError(401, "invalid or expired session");
    }
    const users = await loadUsersUnlocked();
    const user = users[normalizeUsername(session.username)];
    if (!user || !user.is_active) {
      throw new LocalAuthError(401, "invalid or expired session");
    }
    if (Math.max(Number(user.session_version || 0), 0) !== session.sessionVersion) {
      throw new LocalAuthError(401, "invalid or expired session");
    }

    const currentPassword = requirePassword(oldPassword);
    const nextPassword = validateNewPassword(newPassword);
    if (!verifyPassword(currentPassword, user.password_hash)) {
      throw new LocalAuthError(400, "old password is incorrect");
    }
    if (verifyPassword(nextPassword, user.password_hash)) {
      throw new LocalAuthError(400, "new password must be different from old password");
    }

    user.password_hash = hashPassword(nextPassword);
    bumpSessionVersion(user);
    user.updated_at = Date.now() / 1000;
    await saveUsersUnlocked(users);
  });
}

export async function listLocalUsers(): Promise<LocalAuthUser[]> {
  return withFileLock(async () => {
    const users = await loadUsersUnlocked();
    return Object.values(users)
      .map(publicUser)
      .sort((left, right) => left.username.localeCompare(right.username, "en"));
  });
}

export async function createLocalUser(input: { username: string; password: string; is_admin?: boolean }): Promise<LocalAuthUser> {
  return withFileLock(async () => {
    const username = validateUsername(input.username);
    const password = validateNewPassword(input.password);
    const canonical = normalizeUsername(username);
    const users = await loadUsersUnlocked();
    if (users[canonical]) {
      throw new LocalAuthError(400, "username already exists");
    }
    const now = Date.now() / 1000;
    users[canonical] = {
      username,
      password_hash: hashPassword(password),
      is_admin: Boolean(input.is_admin),
      is_active: true,
      created_at: now,
      updated_at: now,
      session_version: 0,
    };
    await saveUsersUnlocked(users);
    return publicUser(users[canonical]);
  });
}

export async function updateLocalUser(
  username: string,
  updates: { is_admin?: boolean; is_active?: boolean; password?: string },
): Promise<LocalAuthUser> {
  return withFileLock(async () => {
    const canonical = normalizeUsername(validateUsername(username));
    const users = await loadUsersUnlocked();
    const user = users[canonical];
    if (!user) {
      throw new LocalAuthError(404, "user not found");
    }

    let changed = false;
    let shouldRevokeSessions = false;

    if (typeof updates.is_admin === "boolean" && user.is_admin !== updates.is_admin) {
      if (user.is_admin && !updates.is_admin && user.is_active && activeAdminCount(users) <= 1) {
        throw new LocalAuthError(400, "at least one active admin must remain");
      }
      user.is_admin = updates.is_admin;
      changed = true;
    }

    if (typeof updates.is_active === "boolean" && user.is_active !== updates.is_active) {
      if (user.is_admin && !updates.is_active && activeAdminCount(users) <= 1) {
        throw new LocalAuthError(400, "at least one active admin must remain");
      }
      user.is_active = updates.is_active;
      changed = true;
      if (!updates.is_active) {
        shouldRevokeSessions = true;
      }
    }

    if (typeof updates.password === "string") {
      user.password_hash = hashPassword(validateNewPassword(updates.password));
      changed = true;
      shouldRevokeSessions = true;
    }

    if (!changed) {
      throw new LocalAuthError(400, "no update fields provided");
    }

    if (shouldRevokeSessions) {
      bumpSessionVersion(user);
    }
    user.updated_at = Date.now() / 1000;
    await saveUsersUnlocked(users);
    return publicUser(user);
  });
}

export async function deleteLocalUser(username: string, actorUsername?: string): Promise<void> {
  await withFileLock(async () => {
    const canonical = normalizeUsername(validateUsername(username));
    const users = await loadUsersUnlocked();
    const user = users[canonical];
    if (!user) {
      throw new LocalAuthError(404, "user not found");
    }
    if (actorUsername && canonical === normalizeUsername(actorUsername)) {
      throw new LocalAuthError(400, "cannot delete current login user");
    }
    if (user.is_admin && user.is_active && activeAdminCount(users) <= 1) {
      throw new LocalAuthError(400, "at least one active admin must remain");
    }
    delete users[canonical];
    await saveUsersUnlocked(users);
  });
}
