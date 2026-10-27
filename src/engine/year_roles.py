"""期间年份识别：先排除金额、百分比和编号角色，再保留真实期间表达。

旧实现 ``(?:19|20)\\d{2}`` 无边界的最大缺陷是把 ``32043.94万元`` 里的
``2043`` 当成年份，从而拒绝同句的决算分子。这里不靠“只加一个字符边界”，
而是同时校验数值角色与期间标记：

- 金额：``2043万元``、``2024.00万元``、``1,923.39元`` 不是年份；
- 百分比/编号：``2023.5%``、``12023`` 不是年份；
- 期间：``2023年``、``2023年度``、``2023、2024年度``、``2023,2024年度``、
  ``2023—2024年度``、``2023至2024年度``；
- 全角数字、全角标点与软换行（``2023\\n年``）按 NFKC 视图识别，再映射回原文偏移。

返回值保留原文起止偏移，规则引用与 bbox 不因归一失准。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import List

#: 4 位年份只排除“同一数值内部”的相邻字符；逗号留给“2023,2024年度”这类
#: 年度列表处理（19xx/20xx 是 4 位，不可能是三位千分位组）。
_YEAR_CANDIDATE = re.compile(r"(?<![\d.%])((?:19|20)\d{2})(?![\d.%])")
_YEAR_AT = re.compile(r"(?:19|20)\d{2}")
_LIST_SEPARATORS = "、,，-—–~～至到"
_TRAILING_SPACES = " \t\u3000\xa0\n\r"


@dataclass(frozen=True)
class PeriodYear:
    year: int
    start: int
    end: int


def _normalized_view(text: str):
    chars: List[str] = []
    positions: List[int] = []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            chars.append(normalized)
            positions.append(index)
    return "".join(chars), positions


def _skip_spaces(text: str, position: int) -> int:
    while position < len(text) and text[position] in _TRAILING_SPACES:
        position += 1
    return position


def find_period_year_tokens(text: str) -> List[PeriodYear]:
    """返回原文中的真实期间年份及其原文偏移（按出现顺序）。"""
    source = str(text or "")
    if not source:
        return []
    normalized, positions = _normalized_view(source)
    tokens: List[PeriodYear] = []
    for match in _YEAR_CANDIDATE.finditer(normalized):
        year = int(match.group(1))
        after = _skip_spaces(normalized, match.end())
        if normalized.startswith("年", after):
            start = positions[match.start()]
            marker_end = after + (1 if normalized.startswith("年度", after) else 0)
            end = positions[marker_end] + 1
            tokens.append(PeriodYear(year=year, start=start, end=end))
            continue
        # 年度列表：2023、2024年度 / 2023,2024年度 / 2023—2024年度 / 2023至2024年度。
        if after < len(normalized) and normalized[after] in _LIST_SEPARATORS:
            following_start = _skip_spaces(normalized, after + 1)
            following = _YEAR_AT.match(normalized, following_start)
            if following is None:
                continue
            following_after = _skip_spaces(normalized, following.end())
            if not normalized.startswith("年", following_after):
                continue
            start = positions[match.start()]
            marker_end = following_after + (1 if normalized.startswith("年度", following_after) else 0)
            end = positions[marker_end] + 1
            tokens.append(PeriodYear(year=year, start=start, end=end))
    return tokens


def foreign_period_years(text: str, fiscal_year: int) -> List[PeriodYear]:
    """与材料年度不同的真实期间年份（金额/百分比/编号不参与）。"""
    return [token for token in find_period_year_tokens(text) if token.year != fiscal_year]


__all__ = ["PeriodYear", "find_period_year_tokens", "foreign_period_years"]
