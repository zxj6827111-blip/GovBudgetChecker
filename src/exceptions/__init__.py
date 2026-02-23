"""
Unified Exception Handling Module

Provides centralized exception handling with proper error codes and messages.
"""
import logging
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    SUCCESS = "SUCCESS"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_ALREADY_EXISTS = "RESOURCE_ALREADY_EXISTS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    INVALID_FILE_CONTENT = "INVALID_FILE_CONTENT"
    DATABASE_ERROR = "DATABASE_ERROR"
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"
    AI_SERVICE_ERROR = "AI_SERVICE_ERROR"
    AI_SERVICE_TIMEOUT = "AI_SERVICE_TIMEOUT"
    RULE_ENGINE_ERROR = "RULE_ENGINE_ERROR"
    PDF_PARSE_ERROR = "PDF_PARSE_ERROR"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_ALREADY_RUNNING = "JOB_ALREADY_RUNNING"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERROR_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.SUCCESS: "Operation completed successfully",
    ErrorCode.UNKNOWN_ERROR: "An unexpected error occurred",
    ErrorCode.VALIDATION_ERROR: "Input validation failed",
    ErrorCode.AUTHENTICATION_ERROR: "Authentication failed",
    ErrorCode.AUTHORIZATION_ERROR: "Access denied",
    ErrorCode.RESOURCE_NOT_FOUND: "Requested resource not found",
    ErrorCode.RESOURCE_ALREADY_EXISTS: "Resource already exists",
    ErrorCode.RATE_LIMIT_EXCEEDED: "Rate limit exceeded, please try again later",
    ErrorCode.FILE_TOO_LARGE: "Uploaded file exceeds maximum allowed size",
    ErrorCode.INVALID_FILE_TYPE: "File type not allowed",
    ErrorCode.INVALID_FILE_CONTENT: "File content is invalid or corrupted",
    ErrorCode.DATABASE_ERROR: "Database operation failed",
    ErrorCode.DATABASE_CONNECTION_ERROR: "Failed to connect to database",
    ErrorCode.AI_SERVICE_ERROR: "AI service error",
    ErrorCode.AI_SERVICE_TIMEOUT: "AI service request timed out",
    ErrorCode.RULE_ENGINE_ERROR: "Rule engine execution error",
    ErrorCode.PDF_PARSE_ERROR: "Failed to parse PDF document",
    ErrorCode.JOB_NOT_FOUND: "Analysis job not found",
    ErrorCode.JOB_ALREADY_RUNNING: "Analysis job is already running",
    ErrorCode.INVALID_REQUEST: "Invalid request parameters",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
}


@dataclass
class APIError:
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    http_status: int = 500
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
            }
        }
        if self.details:
            result["error"]["details"] = self.details
        return result


class AppException(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str = None,
        details: Dict[str, Any] = None,
        http_status: int = 500,
        log_level: int = logging.ERROR
    ):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "Unknown error")
        self.details = details
        self.http_status = http_status
        self.log_level = log_level
        super().__init__(self.message)
    
    def to_api_error(self) -> APIError:
        return APIError(
            code=self.code,
            message=self.message,
            details=self.details,
            http_status=self.http_status
        )


class ValidationError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
            http_status=400,
            log_level=logging.WARNING
        )


class AuthenticationError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.AUTHENTICATION_ERROR,
            message=message,
            details=details,
            http_status=401,
            log_level=logging.WARNING
        )


class AuthorizationError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.AUTHORIZATION_ERROR,
            message=message,
            details=details,
            http_status=403,
            log_level=logging.WARNING
        )


class NotFoundError(AppException):
    def __init__(self, resource: str = "Resource", resource_id: str = None, details: Dict[str, Any] = None):
        message = f"{resource} not found"
        if resource_id:
            message = f"{resource} with id '{resource_id}' not found"
        super().__init__(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message=message,
            details=details,
            http_status=404,
            log_level=logging.INFO
        )


class RateLimitError(AppException):
    def __init__(self, retry_after: int = 60, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            message=f"Rate limit exceeded. Please retry after {retry_after} seconds",
            details={"retry_after": retry_after, **(details or {})},
            http_status=429,
            log_level=logging.WARNING
        )


class FileValidationError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.INVALID_FILE_TYPE,
            message=message,
            details=details,
            http_status=400,
            log_level=logging.WARNING
        )


class FileTooLargeError(AppException):
    def __init__(self, max_size_mb: int, actual_size_mb: float, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.FILE_TOO_LARGE,
            message=f"File size ({actual_size_mb:.1f}MB) exceeds maximum allowed ({max_size_mb}MB)",
            details={"max_size_mb": max_size_mb, "actual_size_mb": actual_size_mb, **(details or {})},
            http_status=413,
            log_level=logging.WARNING
        )


class DatabaseError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.DATABASE_ERROR,
            message=message,
            details=details,
            http_status=500,
            log_level=logging.ERROR
        )


class AIServiceError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.AI_SERVICE_ERROR,
            message=message,
            details=details,
            http_status=502,
            log_level=logging.ERROR
        )


class PDFParseError(AppException):
    def __init__(self, message: str = None, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.PDF_PARSE_ERROR,
            message=message,
            details=details,
            http_status=422,
            log_level=logging.WARNING
        )


class JobNotFoundError(AppException):
    def __init__(self, job_id: str, details: Dict[str, Any] = None):
        super().__init__(
            code=ErrorCode.JOB_NOT_FOUND,
            message=f"Analysis job '{job_id}' not found",
            details={"job_id": job_id, **(details or {})},
            http_status=404,
            log_level=logging.INFO
        )


def handle_exception(exc: Exception) -> APIError:
    if isinstance(exc, AppException):
        logger.log(exc.log_level, f"{exc.code.value}: {exc.message}", extra={"details": exc.details})
        return exc.to_api_error()
    
    if isinstance(exc, ValueError):
        logger.warning(f"Validation error: {str(exc)}")
        return APIError(
            code=ErrorCode.VALIDATION_ERROR,
            message=str(exc),
            http_status=400
        )
    
    if isinstance(exc, FileNotFoundError):
        logger.warning(f"File not found: {str(exc)}")
        return APIError(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message=str(exc),
            http_status=404
        )
    
    if isinstance(exc, PermissionError):
        logger.error(f"Permission error: {str(exc)}")
        return APIError(
            code=ErrorCode.AUTHORIZATION_ERROR,
            message="Permission denied",
            http_status=403
        )
    
    if isinstance(exc, TimeoutError):
        logger.error(f"Timeout error: {str(exc)}")
        return APIError(
            code=ErrorCode.AI_SERVICE_TIMEOUT,
            message="Operation timed out",
            http_status=504
        )
    
    logger.exception(f"Unhandled exception: {type(exc).__name__}: {str(exc)}")
    return APIError(
        code=ErrorCode.INTERNAL_ERROR,
        message="An internal error occurred. Please try again later.",
        http_status=500
    )


def create_success_response(data: Any, message: str = None) -> Dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "message": message or ERROR_MESSAGES[ErrorCode.SUCCESS]
    }
