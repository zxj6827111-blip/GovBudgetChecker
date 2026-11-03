"""决算公开官方模板的年度事实（按年度 × 部门/单位口径）。

数据放在同目录 ``data/annual_final_template_facts.json``，来源与比对记录见该文件
``_note``/``source`` 以及 ``gold/templates/README.md``。规则只读取这里的事实，
不把年度差异写死在判断里；以后可由模板服务替换同一份接口。

未建模的年份返回 ``None``，调用方保持原有判断（2021 年及以前不改变现有行为）；
2025 年及以后没有新模板，按最近已建模年度（2024）处理。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

_DATA_PATH = Path(__file__).with_name("data") / "annual_final_template_facts.json"
_SCOPES = {"department", "unit"}


@dataclass(frozen=True)
class FinalTemplateFacts:
    """一份决算模板在两个规则维度上的事实。"""

    body_writes_part_number_2: bool
    three_public_table_name: str


@lru_cache(maxsize=1)
def _facts() -> Dict[str, Any]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def normalize_table_name(text: str) -> str:
    """与 CMM-021 的表题归一一致：去空白与引号。"""
    return re.sub(r"[\s“”\"'‘’]", "", str(text or ""))


def final_template_facts(year: Optional[int], scope: Optional[str]) -> Optional[FinalTemplateFacts]:
    """返回该年度、该口径的决算模板事实；未建模年份或口径未知返回 ``None``。"""
    if scope not in _SCOPES:
        return None
    try:
        year_int = int(year)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    table = _facts().get("final") or {}
    years = sorted(int(y) for y in table)
    if not years or year_int < years[0]:
        return None
    modeled = str(year_int) if str(year_int) in table else str(max(y for y in years if y <= year_int))
    entry = (table.get(modeled) or {}).get(scope) or {}
    if "body_writes_part_number_2" not in entry or "three_public_table_name" not in entry:
        return None
    return FinalTemplateFacts(
        body_writes_part_number_2=bool(entry["body_writes_part_number_2"]),
        three_public_table_name=normalize_table_name(entry["three_public_table_name"]),
    )


def body_writes_part_number_2(year: Optional[int], scope: Optional[str]) -> Optional[bool]:
    """正文是否写“第二部分”字样；``None`` 表示该年度未建模，保持现有行为。"""
    facts = final_template_facts(year, scope)
    return None if facts is None else facts.body_writes_part_number_2


def normative_three_public_table_name(year: Optional[int], scope: Optional[str]) -> Optional[str]:
    """第七张表（三公经费支出决算表）的规范表名（已归一）；``None`` 表示未建模。"""
    facts = final_template_facts(year, scope)
    return None if facts is None else facts.three_public_table_name
