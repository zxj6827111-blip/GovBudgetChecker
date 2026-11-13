"""Structural fiscal rows must remain outside the amount fact set."""

from __future__ import annotations

import re
from typing import Iterable

from src.services.fiscal_table_rules import normalize_text


def is_index_header(values: Iterable[object]) -> bool:
    """Recognize explicit row/column index labels, never integer amounts alone."""
    values = list(values)
    if any(
        re.fullmatch(r"(?:栏次|行次)(?:编号)?[:：]?", normalize_text(str(value or "")))
        for value in values
    ):
        return True
    # Scanned forms also mark non-amount columns with **/…/dots before a
    # consecutive 1..N column-number sequence. Require the explicit markers;
    # integer amounts or a real row label alone never satisfy this branch.
    tokens = [str(value or "").strip() for value in values if str(value or "").strip()]
    numbers = [token for token in tokens if token.isdigit()]
    markers = [token for token in tokens if not token.isdigit()]
    return bool(markers and len(numbers) >= 3
                and all(re.fullmatch(r"[*.．。·…⋯]+", marker) for marker in markers)
                and numbers == [str(index) for index in range(1, len(numbers) + 1)])
