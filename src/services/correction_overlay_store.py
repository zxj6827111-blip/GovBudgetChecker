"""Append-only storage for human corrections and quality feedback (ZC-05).

This file is an audit overlay, not a second source of calculated fiscal facts.
The production rule chain does not yet consume FiscalFact overlays, so callers
must not describe a stored correction as a completed reanalysis.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


def current_analysis_basis_token(job_id: str, job_dir: Path) -> Optional[str]:
    """Return a token from an explicit analysis revision, never filesystem time."""
    pointer_path = job_dir / "analysis_versions" / "current.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        revision_id = str(pointer.get("revision_id") or "").strip()
        if revision_id:
            return f"{job_id}:{revision_id}"
    except (OSError, ValueError, TypeError):
        pass

    try:
        status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    revision = status.get("analysis_revision")
    if revision is None or str(revision).strip() == "":
        return None
    return f"{job_id}:{revision}"


def append_record_with_basis_cas(
    job_dir: Path,
    *,
    filename: str,
    expected_basis_token: str,
    record: Mapping[str, Any],
) -> Dict[str, Any]:
    """Lock, compare current basis and append one fsynced JSONL record.

    The lock serializes concurrent writers to this job's overlay. The current
    analysis writer does not participate in this lock, so this is an optimistic
    check against the published revision pointer, not a distributed transaction.
    """
    if Path(filename).name != filename or not filename.endswith(".jsonl"):
        raise ValueError("invalid overlay filename")
    overlay_path = job_dir / filename
    lock_path = job_dir / f".{filename}.lock"
    job_dir.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )

    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        current_token = current_analysis_basis_token(job_dir.name, job_dir)
        if current_token is None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return {"accepted": False, "reason": "analysis_basis_unavailable"}
        if current_token != expected_basis_token:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return {
                "accepted": False,
                "reason": "analysis_basis_changed",
                "current_basis_token": current_token,
            }
        descriptor = os.open(overlay_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError("short append while writing audit overlay")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return {"accepted": True}


def read_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    """Read valid JSONL records, preserving append order and ignoring no bad lines."""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"invalid record at line {line_number}")
            records.append(value)
    return records


def iter_evidence_refs(value: Any) -> Iterable[Dict[str, Any]]:
    """Yield explicit evidence objects from a JSON-compatible report tree."""
    if isinstance(value, Mapping):
        evidence_id = str(value.get("evidence_id") or "").strip()
        if evidence_id:
            yield dict(value)
        for child in value.values():
            yield from iter_evidence_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_evidence_refs(child)


def report_evidence_ids(payload: Any) -> set[str]:
    return {
        str(item.get("evidence_id") or "").strip()
        for item in iter_evidence_refs(payload)
        if str(item.get("evidence_id") or "").strip()
    }


def affected_check_ids(check_receipts: Any, target_ids: Iterable[str]) -> Dict[str, Any]:
    """Match only explicit fact dependency fields; never infer by title or amount."""
    target_set = {str(item) for item in target_ids if str(item)}
    if not isinstance(check_receipts, list):
        return {"status": "unknown", "check_ids": [], "reason": "报告没有子检查回执"}

    dependency_keys = {
        "fact_uid",
        "fact_id",
        "target_fact_id",
        "fact_ids",
        "required_fact_ids",
        "operand_fact_uids",
        "operand_ids",
    }

    def collect(node: Any, key: str = "") -> set[str]:
        found: set[str] = set()
        if isinstance(node, Mapping):
            for child_key, child in node.items():
                normalized_key = str(child_key).lower()
                if normalized_key in dependency_keys:
                    if isinstance(child, list):
                        found.update(str(item) for item in child)
                    elif child is not None:
                        found.add(str(child))
                found.update(collect(child, normalized_key))
        elif isinstance(node, list):
            for child in node:
                found.update(collect(child, key))
        return found

    affected: List[str] = []
    for receipt in check_receipts:
        if not isinstance(receipt, Mapping) or not (collect(receipt) & target_set):
            continue
        check_id = str(receipt.get("sub_check_id") or receipt.get("check_id") or "").strip()
        if check_id and check_id not in affected:
            affected.append(check_id)
    if affected:
        return {"status": "mapped", "check_ids": affected, "reason": None}
    return {
        "status": "unknown",
        "check_ids": [],
        "reason": "当前回执未包含事实依赖 ID，无法安全缩小重算范围",
    }


__all__ = [
    "affected_check_ids",
    "append_record_with_basis_cas",
    "current_analysis_basis_token",
    "iter_evidence_refs",
    "read_jsonl_records",
    "report_evidence_ids",
]
