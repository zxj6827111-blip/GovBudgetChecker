"""Shared frozen-truth identity contract for quotas and evaluation.

Canonical IDs are exact. Only the historical T<number><one letter> notation
is supported implicitly; arbitrary English tails never identify evidence faces.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


def canonical_truth_group(annotation: Mapping[str, Any]) -> str:
    explicit = str(annotation.get("truth_group_id") or "").strip()
    if explicit:
        return explicit
    truth_id = str(annotation.get("truth_id") or "").strip()
    if not truth_id:
        return str(annotation.get("annotation_id") or "").strip()
    legacy = re.fullmatch(r"(T[0-9]+)[a-z]", truth_id)
    return legacy.group(1) if legacy else truth_id
