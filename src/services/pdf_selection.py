"""Canonical PDF selection for upload-task artifacts.

An upload directory may contain the original PDF together with one or more
derived/annotated PDFs.  Callers must use the filename recorded by the task
metadata when more than one candidate exists; alphabetical order is not an
identity rule and can silently select the wrong document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Set


def _basename(value: Any) -> str:
    text = str(value or "").strip()
    return re.split(r"[\\/]", text)[-1] if text else ""


def _read_status(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def select_canonical_pdf(job_dir: Path) -> Path:
    """Return the uniquely identified PDF, or fail closed if ambiguous.

    Both ``filename`` and ``saved_path`` are considered evidence.  If they
    identify different files, or identify none of the PDFs on disk, the task
    is rejected instead of guessing.  A single on-disk PDF remains
    unambiguous even when old metadata is incomplete.
    """

    pdfs = sorted(
        (
            child
            for child in job_dir.iterdir()
            if child.is_file() and child.suffix.lower() == ".pdf"
        ),
        key=lambda item: item.name,
    ) if job_dir.is_dir() else []
    if not pdfs:
        raise FileNotFoundError("PDF file not found under job directory")
    if len(pdfs) == 1:
        return pdfs[0]

    status = _read_status(job_dir / "status.json")
    recorded_names: Set[str] = {
        name
        for key in ("filename", "saved_path")
        for name in (_basename(status.get(key)),)
        if name
    }
    matches = [pdf for pdf in pdfs if pdf.name in recorded_names]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            "Multiple PDF files are independently referenced by status.json"
        )
    raise ValueError(
        "Multiple PDF files found without a unique filename/saved_path in status.json"
    )
