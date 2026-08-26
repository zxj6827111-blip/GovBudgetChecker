"""
API Security Module

Provides authentication, authorization, and security utilities for the API.
"""
import os
import secrets
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass, field
from functools import wraps
import logging

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"
API_KEY_ENV = "GOVBUDGET_API_KEY"
API_KEY_LENGTH = 32
DEFAULT_RATE_LIMIT = 100
RATE_LIMIT_WINDOW = 60
TRUST_PROXY_HEADERS_ENV = "GOVBUDGET_TRUST_PROXY_HEADERS"
TRUSTED_PROXY_IPS_ENV = "GOVBUDGET_TRUSTED_PROXY_IPS"


def _env_flag(name: str, default: bool) -> bool:
    """读取布尔型环境变量；未设置时用默认值。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class RateLimitEntry:
    request_count: int = 0
    window_start: float = 0.0
    blocked_until: float = 0.0


@dataclass
class SecurityConfig:
    enabled: bool = True
    api_key: Optional[str] = None
    rate_limit: int = DEFAULT_RATE_LIMIT
    rate_limit_window: int = RATE_LIMIT_WINDOW
    exempt_paths: Set[str] = field(default_factory=lambda: {
        "/health",
        "/api/health",
        "/ready",
        "/api/ready",
        "/docs",
        "/openapi.json",
        "/redoc",
    })
    admin_api_keys: Set[str] = field(default_factory=set)
    trust_proxy_headers: bool = False
    trusted_proxy_ips: Set[str] = field(default_factory=set)


class APIKeyManager:
    def __init__(self, config: SecurityConfig):
        self.config = config
        self._rate_limits: Dict[str, RateLimitEntry] = {}
        self._api_keys: Set[str] = set()
        self._initialize_keys()
    
    def _initialize_keys(self):
        if self.config.api_key:
            self._api_keys.add(self.config.api_key)
            logger.info("API key loaded from configuration")
        
        for key in self.config.admin_api_keys:
            self._api_keys.add(key)
        
        if not self._api_keys and self.config.enabled:
            raise RuntimeError(
                "Authentication is enabled but no API key is configured. "
                "Set GOVBUDGET_API_KEY before starting the service."
            )
    
    def validate_key(self, api_key: str) -> bool:
        if not self.config.enabled:
            return True
        return api_key in self._api_keys
    
    def check_rate_limit(self, client_id: str) -> tuple[bool, int]:
        if not self.config.enabled:
            return True, 0
        
        now = time.time()
        entry = self._rate_limits.get(client_id)
        
        if entry and entry.blocked_until > now:
            remaining = int(entry.blocked_until - now)
            return False, remaining
        
        if entry is None:
            entry = RateLimitEntry(request_count=1, window_start=now)
            self._rate_limits[client_id] = entry
            return True, self.config.rate_limit - 1
        
        if now - entry.window_start > self.config.rate_limit_window:
            entry.request_count = 1
            entry.window_start = now
            entry.blocked_until = 0.0
            return True, self.config.rate_limit - 1
        
        entry.request_count += 1
        remaining = self.config.rate_limit - entry.request_count
        
        if remaining < 0:
            entry.blocked_until = now + 60
            return False, 60
        
        return True, remaining
    
    def generate_key(self) -> str:
        new_key = secrets.token_urlsafe(API_KEY_LENGTH)
        self._api_keys.add(new_key)
        return new_key
    
    def revoke_key(self, api_key: str) -> bool:
        if api_key in self._api_keys:
            self._api_keys.remove(api_key)
            return True
        return False


def create_security_config() -> SecurityConfig:
    api_key = os.getenv(API_KEY_ENV)
    
    admin_keys_str = os.getenv("GOVBUDGET_ADMIN_API_KEYS", "")
    admin_keys = {k.strip() for k in admin_keys_str.split(",") if k.strip()}
    trusted_proxy_ips_str = os.getenv(TRUSTED_PROXY_IPS_ENV, "")
    trusted_proxy_ips = {
        ip.strip() for ip in trusted_proxy_ips_str.split(",") if ip.strip()
    }
    
    rate_limit = int(os.getenv("GOVBUDGET_RATE_LIMIT", str(DEFAULT_RATE_LIMIT)))
    trust_proxy_headers = os.getenv(TRUST_PROXY_HEADERS_ENV, "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    
    testing_mode = os.getenv("TESTING", "").lower() in {"1", "true", "yes"}
    auth_env = os.getenv("GOVBUDGET_AUTH_ENABLED")
    if auth_env is None and testing_mode:
        enabled = False
    else:
        enabled = (auth_env or "true").lower() != "false"

    if enabled and not api_key and not testing_mode:
        raise RuntimeError(
            "GOVBUDGET_AUTH_ENABLED=true but GOVBUDGET_API_KEY is not set. "
            "Refusing to start for production safety."
        )
    
    return SecurityConfig(
        enabled=enabled,
        api_key=api_key,
        rate_limit=rate_limit,
        admin_api_keys=admin_keys,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxy_ips=trusted_proxy_ips,
    )


security_config = create_security_config()
api_key_manager = APIKeyManager(security_config)

security_scheme = HTTPBearer(auto_error=False)


async def get_current_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)
) -> Optional[str]:
    api_key = request.headers.get(API_KEY_HEADER)
    
    if not api_key and credentials:
        api_key = credentials.credentials
    
    return api_key


async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Depends(get_current_api_key)
) -> str:
    if not security_config.enabled:
        return "anonymous"
    
    if request.url.path in security_config.exempt_paths:
        return "exempt"
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header or Authorization: Bearer <key>"
        )
    
    if not api_key_manager.validate_key(api_key):
        logger.warning(f"Invalid API key attempt from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )
    
    return api_key


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        config: SecurityConfig = None,
        manager: Optional[APIKeyManager] = None,
    ):
        super().__init__(app)
        self.config = config or security_config
        self.manager = manager or api_key_manager
    
    async def dispatch(self, request: Request, call_next):
        # Let CORS preflight pass through before auth/rate-limit checks.
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        if request.url.path in self.config.exempt_paths:
            return await call_next(request)

        if self._is_local_file_preview_request(request):
            return await call_next(request)
        
        client_id = self._get_client_id(request)
        
        allowed, remaining = self.manager.check_rate_limit(client_id)
        
        if not allowed:
            logger.warning(f"Rate limit exceeded for client: {client_id}")
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "detail": "Too many requests. Please try again later.",
                    "retry_after": remaining
                },
                headers={"Retry-After": str(remaining)}
            )

        if self.config.enabled:
            api_key = self._extract_api_key(request)
            if not api_key:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "authentication_required",
                        "detail": "API key required. Provide X-API-Key header or Authorization: Bearer <key>",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if not self.manager.validate_key(api_key):
                logger.warning("Invalid API key attempt from %s", client_id)
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={"error": "invalid_api_key", "detail": "Invalid API key"},
                )
        
        response = await call_next(request)
        
        response.headers["X-RateLimit-Limit"] = str(self.config.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        
        return response
    
    def _get_client_id(self, request: Request) -> str:
        if self.config.trust_proxy_headers and self._is_trusted_proxy(request):
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                forwarded_ip = forwarded.split(",")[0].strip()
                if forwarded_ip:
                    return forwarded_ip
        
        if request.client:
            return request.client.host
        
        return "unknown"

    def _is_trusted_proxy(self, request: Request) -> bool:
        if request.client is None:
            return False
        if not self.config.trusted_proxy_ips:
            return True
        return request.client.host in self.config.trusted_proxy_ips

    def _extract_api_key(self, request: Request) -> Optional[str]:
        key = request.headers.get(API_KEY_HEADER)
        if key:
            return key
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                return token
        return None

    def _is_local_file_preview_request(self, request: Request) -> bool:
        if request.method.upper() != "GET":
            return False

        path = request.url.path
        if not path.startswith("/api/files/"):
            return False
        if not (path.endswith("/preview") or path.endswith("/source")):
            return False

        client_host = request.client.host if request.client else ""
        return client_host in {"127.0.0.1", "::1", "localhost"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """注入安全响应头（缺口 B-08）。

    整改前全仓没有任何 HSTS / CSP / X-Content-Type-Options / X-Frame-Options。

    设计要点：
    - 本中间件必须是**最外层**，这样连免认证端点（`/health`、`/ready`、`/docs`）
      的响应也带上安全头；`api/main.py` 里在 `SecurityMiddleware` 之后添加即可
      （Starlette 的 `add_middleware` 是前插，后加的跑在外层）。
    - `/docs`、`/redoc`、`/openapi.json` 需要单独一份宽松 CSP：Swagger UI 会从
      CDN 加载脚本样式并执行内联初始化脚本，用 API 那套 `default-src 'none'`
      会把文档页面打成白屏。
    - HSTS 默认只在 HTTPS 请求上下发（含反代的 `X-Forwarded-Proto: https`）。
      本地 http 开发下发 HSTS 会让浏览器把 localhost 记成强制 HTTPS，得手动清缓存。
    - 前端由 Next.js 独立进程提供，它的 CSP 在 `app/next.config.js` 里配置，
      与本中间件互不覆盖。
    """

    #: 文档类路径前缀，使用宽松 CSP
    DOCS_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json")

    #: API 响应的默认 CSP：这些响应是 JSON 与 PNG，不需要脚本
    DEFAULT_CSP = (
        "default-src 'none'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )

    #: 文档页 CSP：允许 Swagger UI 的 CDN 资源与内联初始化脚本
    DOCS_CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://cdn.jsdelivr.net; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    DEFAULT_HSTS = "max-age=31536000; includeSubDomains"

    def __init__(self, app):
        super().__init__(app)
        self.enabled = _env_flag("SECURITY_HEADERS_ENABLED", True)
        self.csp = os.getenv("SECURITY_CSP", "").strip() or self.DEFAULT_CSP
        self.docs_csp = os.getenv("SECURITY_CSP_DOCS", "").strip() or self.DOCS_CSP
        self.hsts = os.getenv("SECURITY_HSTS", "").strip() or self.DEFAULT_HSTS
        # 显式打开时无条件下发 HSTS（例如 TLS 终止在更外层、请求头也被剥掉的场景）
        self.force_hsts = _env_flag("SECURITY_HSTS_ALWAYS", False)
        self.frame_options = os.getenv("SECURITY_FRAME_OPTIONS", "").strip() or "DENY"
        self.referrer_policy = (
            os.getenv("SECURITY_REFERRER_POLICY", "").strip() or "no-referrer"
        )

    def _is_docs_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.DOCS_PATH_PREFIXES)

    def _is_https(self, request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        forwarded = str(request.headers.get("X-Forwarded-Proto") or "").strip().lower()
        return forwarded.split(",")[0].strip() == "https"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not self.enabled:
            return response

        headers = response.headers
        # setdefault 语义：不覆盖路由已显式设置的同名头
        if "X-Content-Type-Options" not in headers:
            headers["X-Content-Type-Options"] = "nosniff"
        if "X-Frame-Options" not in headers:
            headers["X-Frame-Options"] = self.frame_options
        if "Referrer-Policy" not in headers:
            headers["Referrer-Policy"] = self.referrer_policy
        if "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = (
                self.docs_csp if self._is_docs_path(request.url.path) else self.csp
            )
        if "Strict-Transport-Security" not in headers and (
            self.force_hsts or self._is_https(request)
        ):
            headers["Strict-Transport-Security"] = self.hsts
        return response


def sanitize_filename(filename: str) -> str:
    import re
    filename = os.path.basename(filename)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    filename = re.sub(r'\.{2,}', '.', filename)
    filename = filename.strip('. ')
    
    if not filename:
        filename = "unnamed_file"
    
    max_length = 255
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext
    
    return filename


ALLOWED_MIME_TYPES = {
    'application/pdf',
    'application/x-pdf',
}

ALLOWED_EXTENSIONS = {'.pdf'}

MAX_FILE_SIZE_MB = 30

def validate_upload_metadata(
    filename: str,
    content_type: str,
) -> tuple[bool, str]:
    if not filename:
        return False, "Filename is required"

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"File type not allowed. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"

    if content_type and content_type not in ALLOWED_MIME_TYPES:
        return False, f"MIME type '{content_type}' not allowed"

    return True, "OK"


def is_valid_pdf_signature(content: bytes) -> bool:
    return len(content) >= 4 and content.startswith(b"%PDF")


def validate_file_upload(
    filename: str,
    content_type: str,
    content: bytes
) -> tuple[bool, str]:
    metadata_ok, metadata_msg = validate_upload_metadata(filename, content_type)
    if not metadata_ok:
        return metadata_ok, metadata_msg
    
    file_size_mb = len(content) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        return False, f"File size ({file_size_mb:.1f}MB) exceeds maximum allowed ({MAX_FILE_SIZE_MB}MB)"
    
    if len(content) < 4:
        return False, "File is too small to be a valid PDF"
    
    if not is_valid_pdf_signature(content):
        return False, "File does not appear to be a valid PDF (invalid signature)"
    
    return True, "OK"


def mask_sensitive_data(data: str, visible_chars: int = 4) -> str:
    if not data or len(data) <= visible_chars:
        return "***"
    return data[:visible_chars] + "*" * (len(data) - visible_chars)


def log_request_safely(request: Request, api_key: str = None):
    safe_headers = {}
    sensitive_headers = {'authorization', 'x-api-key', 'cookie', 'set-cookie'}
    
    for key, value in request.headers.items():
        if key.lower() in sensitive_headers:
            safe_headers[key] = "***REDACTED***"
        else:
            safe_headers[key] = value
    
    log_data = {
        "method": request.method,
        "path": request.url.path,
        "client": request.client.host if request.client else "unknown",
        "headers": safe_headers,
    }
    
    if api_key:
        log_data["api_key"] = mask_sensitive_data(api_key)
    
    return log_data
