"""年份与金额角色识别：金额不是年份，真实期间与偏移保留。"""
from __future__ import annotations

import pytest

from src.engine.year_roles import find_period_year_tokens, foreign_period_years


@pytest.mark.parametrize("text", [
    "32043.94万元",
    "2043万元",
    "2024.00万元",
    "1,923.39元",
    "2023.5%",
    "12023",
    "1,2023元",
])
def test_amounts_and_numbers_are_not_years(text):
    assert find_period_year_tokens(text) == []


@pytest.mark.parametrize("text, expected", [
    ("2023年", [2023]),
    ("2023年度", [2023]),
    ("2023、2024年度", [2023, 2024]),
    ("2023,2024年度", [2023, 2024]),
    ("2023，2024年度", [2023, 2024]),
    ("2023—2024年度", [2023, 2024]),
    ("2023至2024年度", [2023, 2024]),
    ("２０２３年", [2023]),
    ("2023\n年", [2023]),
    ("比上年2023年增加", [2023]),
    ("2023年12月", [2023]),
    ("项目2023年支出", [2023]),
])
def test_real_periods_are_preserved(text, expected):
    assert [token.year for token in find_period_year_tokens(text)] == expected


def test_offsets_map_back_to_original_text():
    text = "汇编２０２３年及2024年度数据"
    tokens = find_period_year_tokens(text)
    assert [text[token.start:token.end] for token in tokens] == ["２０２３年", "2024年度"]


def test_foreign_comparison_year_is_detected_but_current_year_is_not():
    assert [token.year for token in foreign_period_years("比上年2023年增加12.00万元", 2024)] == [2023]
    assert foreign_period_years("2024年支出决算为2043万元", 2024) == []


def test_list_year_detects_foreign_member():
    assert [token.year for token in foreign_period_years("2023、2024年度支出", 2023)] == [2024]
