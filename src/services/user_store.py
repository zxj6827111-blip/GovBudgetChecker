"""Username/password login and admin-managed user storage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TESTING = os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes"}
_REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_TEST_DATA_DIR = Path(tempfile.gettempdir()) / "govbudgetchecker_test_data"
_DEFAULT_DATA_DIR = _TEST_DATA_DIR if _TESTING else _REPO_DATA_DIR
_HASH_ALGO = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = int(os.getenv("USER_PASSWORD_ITERATIONS", "260000"))
_DEFAULT_PASSWORD_MIN_LENGTH = int(os.getenv("USER_PASSWORD_MIN_LENGTH", "6"))
_SESSION_TOKEN_PREFIX = "gbcs1"


class UserStore:
    """File-backed user store with stateless signed session tokens."""

    def __init__(
        self,
        users_file: Optional[Path] = None,
        session_ttl_seconds: Optional[int] = None,
        default_admin_username: Optional[str] = None,
    ) -> None:
        self._users_file = (
            Path(users_file).resolve()
            if users_file is not None
            else Path(
                os.getenv(
                    "USER_FILE",
                    str(
                        Path(os.getenv("USER_DATA_DIR", str(_DEFAULT_DATA_DIR)))
                        / "users.json"
                    ),
                )
            ).resolve()
        )
        self._session_ttl_seconds = (
            session_ttl_seconds
            if session_ttl_seconds is not None
            else int(os.getenv("USER_SESSION_TTL_SECONDS", "28800"))
        )
        self._password_min_length = _DEFAULT_PASSWORD_MIN_LENGTH
        self._password_iterations = _DEFAULT_ITERATIONS
        self._default_admin_username = (
            default_admin_username
            or os.getenv("DEFAULT_ADMIN_USERNAME", "admin").strip()
            or "admin"
        )
        self._default_admin_password = (
            os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123").strip() or "admin123"
        )
        self._legacy_fallback_password = (
            os.getenv("DEFAULT_LEGACY_PASSWORD", "change_me_123").strip()
            or "change_me_123"
        )
        self._session_secret = self._resolve_session_secret()

        self._lock = threading.RLock()
        self._users: Dict[str, Dict[str, Any]] = {}

        self._ensure_data_dir()
        self._load_users()

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _validate_username(username: str) -> str:
        text = str(username or "").strip()
        if not text:
            raise ValueError("username is required")
        if len(text) > 64:
            raise ValueError("username is too long (max 64 chars)")
        if any(ch.isspace() for ch in text):
            raise ValueError("username cannot contain spaces")
        return text

    def _require_password(self, password: str) -> str:
        text = str(password or "")
        if not text:
            raise ValueError("password is required")
        if len(text) > 256:
            raise ValueError("password is too long")
        return text

    def _validate_new_password(self, password: str) -> str:
        text = self._require_password(password)
        if len(text) < self._password_min_length:
            raise ValueError(
                f"password is too short (min {self._password_min_length} chars)"
            )
        return text

    @staticmethod
    def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "username": str(user.get("username") or ""),
            "is_admin": bool(user.get("is_admin")),
            "is_active": bool(user.get("is_active", True)),
            "created_at": float(user.get("created_at") or 0.0),
            "updated_at": float(user.get("updated_at") or 0.0),
        }

    @staticmethod
    def _coerce_session_version(value: Any) -> int:
        try:
            session_version = int(value)
        except (TypeError, ValueError):
            return 0
        return max(session_version, 0)

    @staticmethod
    def _serialize_user(user: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "username": str(user.get("username") or ""),
            "password_hash": str(user.get("password_hash") or ""),
            "is_admin": bool(user.get("is_admin")),
            "is_active": bool(user.get("is_active", True)),
            "created_at": float(user.get("created_at") or 0.0),
            "updated_at": float(user.get("updated_at") or 0.0),
            "session_version": UserStore._coerce_session_version(
                user.get("session_version")
            ),
        }

    @staticmethod
    def _is_password_hash(value: str) -> bool:
        parts = value.split("$", 3)
        return len(parts) == 4 and parts[0] == _HASH_ALGO

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self._password_iterations,
        )
        salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
        digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
        return f"{_HASH_ALGO}${self._password_iterations}${salt_text}${digest_text}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        if not self._is_password_hash(password_hash):
            return False

        algo, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algo != _HASH_ALGO:
            return False

        try:
            iterations = int(iterations_text)
            salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
            expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        except Exception:
            return False

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(candidate, expected)

    def _resolve_loaded_password_hash(
        self,
        canonical_username: str,
        username: str,
        raw_password_hash: str,
        legacy_password: str,
    ) -> Tuple[str, bool]:
        if raw_password_hash and self._is_password_hash(raw_password_hash):
            return raw_password_hash, False

        if legacy_password:
            password = self._require_password(legacy_password)
            return self._hash_password(password), True

        default_admin_canonical = self._normalize_username(self._default_admin_username)
        if canonical_username == default_admin_canonical:
            return self._hash_password(self._default_admin_password), True

        logger.warning(
            "User '%s' has no password hash, assigning legacy fallback password.",
            username,
        )
        return self._hash_password(self._legacy_fallback_password), True

    def _resolve_session_secret(self) -> bytes:
        raw_secret = (
            os.getenv("USER_SESSION_SECRET", "").strip()
            or os.getenv("GOVBUDGET_API_KEY", "").strip()
        )
        if raw_secret:
            return raw_secret.encode("utf-8")

        fallback = "test-user-session-secret" if _TESTING else "dev-user-session-secret"
        if not _TESTING:
            logger.warning(
                "USER_SESSION_SECRET and GOVBUDGET_API_KEY are both empty; "
                "falling back to a development-only session secret."
            )
        return fallback.encode("utf-8")

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _b64url_decode(text: str) -> bytes:
        padding = "=" * (-len(text) % 4)
        return base64.urlsafe_b64decode((text + padding).encode("ascii"))

    def _sign_session_body(self, body_text: str) -> str:
        signature = hmac.new(
            self._session_secret,
            body_text.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return self._b64url_encode(signature)

    def _build_session_token_locked(self, user: Dict[str, Any]) -> str:
        now = int(time.time())
        payload = {
            "u": str(user.get("username") or ""),
            "iat": now,
            "exp": now + self._session_ttl_seconds,
            "sv": self._coerce_session_version(user.get("session_version")),
        }
        body_text = self._b64url_encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        signature = self._sign_session_body(body_text)
        return f"{_SESSION_TOKEN_PREFIX}.{body_text}.{signature}"

    def _parse_session_token_locked(
        self,
        token: str,
        *,
        allow_expired: bool = False,
    ) -> Optional[Dict[str, Any]]:
        text = str(token or "").strip()
        if not text:
            return None

        parts = text.split(".")
        if len(parts) != 3 or parts[0] != _SESSION_TOKEN_PREFIX:
            return None

        body_text, signature_text = parts[1], parts[2]
        expected_signature = self._sign_session_body(body_text)
        if not hmac.compare_digest(signature_text, expected_signature):
            return None

        try:
            payload = json.loads(self._b64url_decode(body_text).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None

        username = str(payload.get("u") or "").strip()
        if not username:
            return None

        try:
            issued_at = int(payload.get("iat") or 0)
            expires_at = int(payload.get("exp") or 0)
        except (TypeError, ValueError):
            return None
        if issued_at <= 0 or expires_at <= 0 or expires_at < issued_at:
            return None
        if not allow_expired and expires_at <= int(time.time()):
            return None

        return {
            "username": username,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "session_version": self._coerce_session_version(payload.get("sv")),
        }

    def _ensure_data_dir(self) -> None:
        self._users_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_users(self) -> None:
        with self._lock:
            self._load_users_locked()

    def _load_users_locked(self) -> None:
        self._users = {}
        changed = False

        if self._users_file.exists():
            try:
                payload = json.loads(self._users_file.read_text(encoding="utf-8"))
                rows = payload.get("users", [])
                if isinstance(rows, list):
                    now = time.time()
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        username = str(row.get("username") or "").strip()
                        canonical = self._normalize_username(username)
                        if not canonical:
                            continue
                        created = float(row.get("created_at") or now)
                        updated = float(row.get("updated_at") or created)

                        raw_password_hash = str(row.get("password_hash") or "").strip()
                        legacy_password = str(row.get("password") or "")
                        password_hash, migrated = self._resolve_loaded_password_hash(
                            canonical,
                            username,
                            raw_password_hash,
                            legacy_password,
                        )
                        if migrated:
                            changed = True

                        if "session_version" not in row:
                            changed = True

                        self._users[canonical] = {
                            "username": username,
                            "password_hash": password_hash,
                            "is_admin": bool(row.get("is_admin", False)),
                            "is_active": bool(row.get("is_active", True)),
                            "created_at": created,
                            "updated_at": updated,
                            "session_version": self._coerce_session_version(
                                row.get("session_version")
                            ),
                        }
            except Exception:
                logger.exception("Failed to load user store from %s", self._users_file)
                self._users = {}

        if self._ensure_default_admin_locked():
            changed = True

        if changed:
            self._save_users_locked()

    def _save_users_locked(self) -> None:
        rows = [self._serialize_user(user) for user in self._users.values()]
        rows.sort(key=lambda item: item["username"].lower())
        payload = {
            "users": rows,
            "updated_at": time.time(),
        }

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._users_file.parent),
                prefix=f"{self._users_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.write("\n")
                tmp_file.flush()
                tmp_path = Path(tmp_file.name)
            if tmp_path is not None:
                tmp_path.replace(self._users_file)
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _ensure_default_admin_locked(self) -> bool:
        canonical = self._normalize_username(self._default_admin_username)
        now = time.time()
        user = self._users.get(canonical)
        changed = False

        if user is None:
            self._users[canonical] = {
                "username": self._default_admin_username,
                "password_hash": self._hash_password(self._default_admin_password),
                "is_admin": True,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
                "session_version": 0,
            }
            changed = True
            logger.info("Seeded default admin account: %s", self._default_admin_username)
        else:
            if not user.get("is_admin", False):
                user["is_admin"] = True
                changed = True
            if not user.get("is_active", True):
                user["is_active"] = True
                changed = True
            current_hash = str(user.get("password_hash") or "")
            if not self._is_password_hash(current_hash):
                user["password_hash"] = self._hash_password(self._default_admin_password)
                changed = True
            if "session_version" not in user:
                user["session_version"] = 0
                changed = True
            if changed:
                user["updated_at"] = now

        return changed

    @staticmethod
    def _bump_session_version_locked(user: Dict[str, Any]) -> None:
        current = UserStore._coerce_session_version(user.get("session_version"))
        user["session_version"] = current + 1

    def _active_admin_count_locked(self) -> int:
        return sum(
            1
            for user in self._users.values()
            if bool(user.get("is_admin")) and bool(user.get("is_active", True))
        )

    def list_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._load_users_locked()
            users = [self._public_user(user) for user in self._users.values()]
            users.sort(key=lambda item: item["username"].lower())
            return users

    def add_user(
        self,
        username: str,
        password: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        clean_username = self._validate_username(username)
        clean_password = self._validate_new_password(password)
        canonical = self._normalize_username(clean_username)
        now = time.time()

        with self._lock:
            self._load_users_locked()
            if canonical in self._users:
                raise ValueError("username already exists")

            self._users[canonical] = {
                "username": clean_username,
                "password_hash": self._hash_password(clean_password),
                "is_admin": bool(is_admin),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
                "session_version": 0,
            }
            self._save_users_locked()
            return self._public_user(self._users[canonical])

    def update_user(
        self,
        username: str,
        is_admin: Optional[bool] = None,
        is_active: Optional[bool] = None,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        canonical = self._normalize_username(self._validate_username(username))

        with self._lock:
            self._load_users_locked()
            user = self._users.get(canonical)
            if user is None:
                raise KeyError("user not found")

            changed = False
            should_revoke_sessions = False

            if is_admin is not None:
                new_is_admin = bool(is_admin)
                if bool(user.get("is_admin")) != new_is_admin:
                    if (
                        user.get("is_admin")
                        and not new_is_admin
                        and bool(user.get("is_active", True))
                        and self._active_admin_count_locked() <= 1
                    ):
                        raise ValueError("at least one active admin must remain")
                    user["is_admin"] = new_is_admin
                    changed = True

            if is_active is not None:
                new_is_active = bool(is_active)
                if bool(user.get("is_active", True)) != new_is_active:
                    if (
                        user.get("is_admin")
                        and not new_is_active
                        and self._active_admin_count_locked() <= 1
                    ):
                        raise ValueError("at least one active admin must remain")
                    user["is_active"] = new_is_active
                    changed = True
                    if not new_is_active:
                        should_revoke_sessions = True

            if password is not None:
                clean_password = self._validate_new_password(password)
                user["password_hash"] = self._hash_password(clean_password)
                should_revoke_sessions = True
                changed = True

            if changed:
                if should_revoke_sessions:
                    self._bump_session_version_locked(user)
                user["updated_at"] = time.time()
                self._save_users_locked()

            return self._public_user(user)

    def delete_user(self, username: str, actor_username: Optional[str] = None) -> None:
        canonical = self._normalize_username(self._validate_username(username))

        with self._lock:
            self._load_users_locked()
            user = self._users.get(canonical)
            if user is None:
                raise KeyError("user not found")

            if actor_username and canonical == self._normalize_username(actor_username):
                raise ValueError("cannot delete current login user")

            if (
                user.get("is_admin")
                and bool(user.get("is_active", True))
                and self._active_admin_count_locked() <= 1
            ):
                raise ValueError("at least one active admin must remain")

            del self._users[canonical]
            self._save_users_locked()

    def login(self, username: str, password: str) -> Tuple[str, Dict[str, Any]]:
        canonical = self._normalize_username(self._validate_username(username))
        plain_password = self._require_password(password)

        with self._lock:
            self._load_users_locked()
            user = self._users.get(canonical)
            if user is None:
                raise KeyError("user not found")
            if not bool(user.get("is_active", True)):
                raise PermissionError("user is disabled")

            password_hash = str(user.get("password_hash") or "")
            if not self._verify_password(plain_password, password_hash):
                raise PermissionError("invalid username or password")

            token = self._build_session_token_locked(user)
            return token, self._public_user(user)

    def change_password(
        self,
        username: str,
        old_password: str,
        new_password: str,
    ) -> None:
        canonical = self._normalize_username(self._validate_username(username))
        old_plain = self._require_password(old_password)
        new_plain = self._validate_new_password(new_password)

        with self._lock:
            self._load_users_locked()
            user = self._users.get(canonical)
            if user is None:
                raise KeyError("user not found")
            if not bool(user.get("is_active", True)):
                raise PermissionError("user is disabled")

            password_hash = str(user.get("password_hash") or "")
            if not self._verify_password(old_plain, password_hash):
                raise PermissionError("invalid old password")

            if self._verify_password(new_plain, password_hash):
                raise ValueError("new password must be different from old password")

            user["password_hash"] = self._hash_password(new_plain)
            self._bump_session_version_locked(user)
            user["updated_at"] = time.time()
            self._save_users_locked()

    def revoke_session(self, token: str) -> None:
        if not token:
            return

        with self._lock:
            payload = self._parse_session_token_locked(token, allow_expired=True)
            if payload is None:
                return

            self._load_users_locked()
            canonical = self._normalize_username(str(payload.get("username") or ""))
            user = self._users.get(canonical)
            if user is None:
                return

            current_version = self._coerce_session_version(user.get("session_version"))
            token_version = self._coerce_session_version(payload.get("session_version"))
            if current_version != token_version:
                return

            self._bump_session_version_locked(user)
            user["updated_at"] = time.time()
            self._save_users_locked()

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None

        with self._lock:
            payload = self._parse_session_token_locked(token)
            if payload is None:
                return None

            self._load_users_locked()
            canonical = self._normalize_username(str(payload.get("username") or ""))
            user = self._users.get(canonical)
            if user is None or not bool(user.get("is_active", True)):
                return None

            current_version = self._coerce_session_version(user.get("session_version"))
            token_version = self._coerce_session_version(payload.get("session_version"))
            if current_version != token_version:
                return None

            return self._public_user(user)


_store_instance: Optional[UserStore] = None


def get_user_store() -> UserStore:
    """Return the process-wide user store singleton."""
    global _store_instance
    if _store_instance is None:
        _store_instance = UserStore()
    return _store_instance


def reset_user_store() -> None:
    """Reset singleton for tests."""
    global _store_instance
    _store_instance = None
