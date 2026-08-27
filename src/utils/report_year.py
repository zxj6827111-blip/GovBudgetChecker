"""报告年度解析的唯一权威实现。

抽成独立模块的原因：此前内存路径（api/runtime.parse_report_year）与结构化入库路径
（src/services/structured_ingest_runner._parse_year）各写了一份年份解析，
前者识别失败返回 None、后者兜底成 2000，导致 PostgreSQL 的 fiscal_year 被写成 2000
（缺口 B-02 / P0-03）。两条路径共用本模块后，口径不可能再次漂移。

核心口径：识别不到年份就返回 None，绝不兜底成任何具体年份。
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

#: 四位年份，如 2025；限定 20xx 是因为预决算材料不会出现其他世纪
_YEAR_4_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
#: 两位年份简写，仅在紧跟"年/年度/预算/决算/budget/final..."等年份语境时才认
_YEAR_2_RE = re.compile(
    r"(?<!\d)(\d{2})(?=\s*(?:\u5e74|\u5e74\u5ea6|\u9884\u7b97|\u51b3\u7b97|budget|final|settlement|accounts|$))",
    re.I,
)

MIN_REPORT_YEAR = 2000
MAX_REPORT_YEAR = 2099


def extract_report_year_candidates(raw: Any) -> List[int]:
    """Extract report year candidates from free-form text."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []

    years: List[int] = []

    if re.fullmatch(r"\d{1,4}", text):
        try:
            value = int(text)
        except Exception:
            value = -1
        if MIN_REPORT_YEAR <= value <= MAX_REPORT_YEAR:
            years.append(value)
        elif 0 <= value <= 99:
            years.append(2000 + value)

    for match in _YEAR_4_RE.finditer(text):
        year = int(match.group(1))
        if MIN_REPORT_YEAR <= year <= MAX_REPORT_YEAR and year not in years:
            years.append(year)

    for match in _YEAR_2_RE.finditer(text):
        year = 2000 + int(match.group(1))
        if MIN_REPORT_YEAR <= year <= MAX_REPORT_YEAR and year not in years:
            years.append(year)

    return years


def parse_report_year(raw: Any) -> Optional[int]:
    """Parse year from arbitrary value and return 4-digit year, or None."""
    for year in extract_report_year_candidates(raw):
        return year
    return None
