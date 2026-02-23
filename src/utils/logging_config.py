"""
Structured Logging Configuration

Provides consistent logging format across the application with JSON output for production.
"""

import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict, Optional
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """JSON-structured log formatter for production environments."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in ('name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'pathname', 'process', 'processName', 'relativeCreated',
                          'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                          'message', 'taskName'):
                log_data[key] = value
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


class SimpleFormatter(logging.Formatter):
    """Human-readable formatter for development."""
    
    def __init__(self):
        super().__init__(
            fmt='%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: Optional[str] = None
) -> None:
    """
    Configure application logging.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON structured logging (for production)
        log_file: Optional file path for log output
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Select formatter
    formatter = StructuredFormatter() if json_format else SimpleFormatter()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name."""
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding fields to log records."""
    
    def __init__(self, logger: logging.Logger, **fields):
        self.logger = logger
        self.fields = fields
        self.old_factory = None
    
    def __enter__(self):
        old_factory = logging.getLogRecordFactory()
        fields = self.fields
        
        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            for key, value in fields.items():
                setattr(record, key, value)
            return record
        
        self.old_factory = old_factory
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_factory:
            logging.setLogRecordFactory(self.old_factory)


# Quick-access logger for common use
def log_qc_execution(run_id: int, findings_count: int, duration_ms: float):
    """Log QC execution metrics."""
    logger = get_logger("qc.execution")
    logger.info(
        f"QC run {run_id} completed: {findings_count} findings in {duration_ms:.0f}ms",
        extra={"run_id": run_id, "findings_count": findings_count, "duration_ms": duration_ms}
    )


def log_job_stage(job_id: int, stage: str, status: str, details: Optional[Dict] = None):
    """Log job stage transition."""
    logger = get_logger("job.orchestrator")
    logger.info(
        f"Job {job_id}: {stage} -> {status}",
        extra={"job_id": job_id, "stage": stage, "status": status, **(details or {})}
    )


def log_parse_result(version_id: int, tables: int, cells: int, errors: int):
    """Log PDF parsing result."""
    logger = get_logger("parse.pdf")
    logger.info(
        f"Parsed version {version_id}: {tables} tables, {cells} cells, {errors} errors",
        extra={"version_id": version_id, "tables": tables, "cells": cells, "errors": errors}
    )
