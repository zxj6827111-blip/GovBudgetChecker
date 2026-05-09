# -*- coding: utf-8 -*-
"""Focused business-regression contracts for budget/final-account samples.

These cases intentionally use normal Chinese OCR/PDF text. A few are marked as
strict xfail to document currently observed business-rule gaps without changing
the production audit rules during the regression-testing phase.
"""

from __future__ import annotations

import pytest

from src.engine.common_rules import CMM005_ComparativeNarrativeLogic
from src.engine.rules_v33 import (
    R33002_NineTablesCheck,
    R33234_NarrativePercentConsistency,
    R33244_Table7_ThreePublicAdvancedCheck,
    build_document,
    parse_number,
)


def _doc(text: str, tables: list[list[list[str]]] | None = None):
    return build_document(
        path="江苏省某单位2024年度部门决算.pdf",
        page_texts=[text],
        page_tables=[tables or []],
        filesize=1024,
    )


STANDARD_FINAL_TABLE_TITLES = [
    "收入支出决算总表",
    "收入决算表",
    "支出决算表",
    "财政拨款收入支出决算总表",
    "一般公共预算财政拨款支出决算表",
    "一般公共预算财政拨款基本支出决算表",
    "一般公共预算财政拨款“三公”经费支出决算表",
    "政府性基金预算财政拨款收入支出决算表",
    "国有资本经营预算财政拨款收入支出决算表",
]


def _three_public_table_row(*, budget_total: str, final_total: str):
    return [
        [
            ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
            [budget_total, final_total, "1", "1", "4", "7", "1", "1", "3", "6", "2", "2"],
        ]
    ]


def test_placeholder_values_are_not_silently_treated_as_zero() -> None:
    assert parse_number("0") == 0.0

    for value in ("", "—", "-", "/", "不适用", None):
        assert parse_number(value) is None


def test_final_accounts_nine_tables_accept_standard_chinese_titles() -> None:
    text = "\n".join(STANDARD_FINAL_TABLE_TITLES)

    issues = R33002_NineTablesCheck().apply(_doc(text))

    assert issues == []


def test_final_accounts_toc_only_does_not_count_as_complete_nine_tables() -> None:
    text = "目录\n" + "\n".join(STANDARD_FINAL_TABLE_TITLES)

    issues = R33002_NineTablesCheck().apply(_doc(text))

    assert any(issue.rule == "V33-002" and "缺失表" in issue.message for issue in issues)


def test_final_accounts_narrative_mentions_do_not_count_as_table_titles() -> None:
    text = "本部门支出决算表相关数据已在情况说明中列示，具体口径详见财政拨款说明。"

    issues = R33002_NineTablesCheck().apply(_doc(text))

    assert any(issue.rule == "V33-002" and issue.location.get("table") == "支出决算表" for issue in issues)


def test_final_accounts_numbered_titles_are_recognized() -> None:
    text = "\n".join(f"表{idx + 1} {title}" for idx, title in enumerate(STANDARD_FINAL_TABLE_TITLES))

    issues = R33002_NineTablesCheck().apply(_doc(text))

    assert issues == []


def test_final_accounts_title_with_line_break_is_recognized() -> None:
    titles = STANDARD_FINAL_TABLE_TITLES.copy()
    titles[3] = "财政拨款收入支出\n决算总表"
    text = "\n".join(titles)

    issues = R33002_NineTablesCheck().apply(_doc(text))

    assert issues == []


def test_completion_rate_mismatch_with_standard_chinese_text_is_flagged() -> None:
    text = "1、一般公共服务支出。年初预算数为100万元，支出决算数为90万元，完成年初预算的110%。"

    issues = R33234_NarrativePercentConsistency().apply(_doc(text))

    assert any(issue.rule == "V33-234" for issue in issues)


def test_completion_rate_100_without_reason_is_accepted() -> None:
    text = "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，完成年初预算的100%。"

    issues = R33234_NarrativePercentConsistency().apply(_doc(text))

    assert issues == []


@pytest.mark.parametrize(
    "text",
    [
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，完成年初预算的120%。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为80万元，完成年初预算的80%。",
    ],
)
def test_completion_rate_deviation_without_reason_is_flagged(text: str) -> None:
    issues = R33234_NarrativePercentConsistency().apply(_doc(text))

    assert any(issue.rule == "V33-234" for issue in issues)


@pytest.mark.parametrize(
    "text",
    [
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，完成年初预算的120%。主要原因是项目进度加快。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为80万元，完成年初预算的80%。由于部分项目结转下年执行。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，完成年初预算的120%。受项目验收进度影响，支出增加。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为80万元，完成年初预算的80%。主要系项目按合同约定分期支付。",
    ],
)
def test_completion_rate_deviation_with_reason_is_not_flagged(text: str) -> None:
    issues = R33234_NarrativePercentConsistency().apply(_doc(text))

    assert not any("未说明原因" in issue.message for issue in issues)


@pytest.mark.parametrize(
    "text",
    [
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，比预算数增加。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，比预算数减少。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，比预算数减少。",
        "1、一般公共服务支出。年初预算数为120万元，支出决算数为100万元，比预算数增加。",
    ],
)
def test_budget_final_direction_mismatch_is_flagged(text: str) -> None:
    issues = CMM005_ComparativeNarrativeLogic().apply(_doc(text))

    assert any(issue.rule == "CMM-005" for issue in issues)


@pytest.mark.parametrize(
    "text",
    [
        "收入决算情况说明。本年收入100万元，比上年增加10万元。",
        "本部门根据工作需要增加预算安排20万元，保障项目正常开展。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，与预算数持平。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，与预算数一致。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为100万元，等于预算数。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，比预算数增加。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，高于预算数。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，超出预算数。",
        "1、一般公共服务支出。年初预算数为100万元，支出决算数为120万元，超过预算数。",
        "1、一般公共服务支出。年初预算数为120万元，支出决算数为100万元，比预算数减少。",
        "1、一般公共服务支出。年初预算数为120万元，支出决算数为100万元，较预算数下降。",
        "1、一般公共服务支出。年初预算数为120万元，支出决算数为100万元，低于预算数。",
        "1、一般公共服务支出。年初预算数为120万元，支出决算数为100万元，少于预算数。",
    ],
)
def test_budget_final_direction_consistent_wording_is_not_flagged(text: str) -> None:
    issues = CMM005_ComparativeNarrativeLogic().apply(_doc(text))

    assert not any(issue.rule == "CMM-005" for issue in issues)


def test_three_public_table_text_mismatch_with_standard_chinese_text_is_flagged() -> None:
    text = (
        "一般公共预算财政拨款“三公”经费支出决算表\n"
        "七、财政拨款三公经费支出决算情况说明。"
        "三公经费支出决算数为11万元，其中因公出国决算数为1万元，"
        "公务用车决算数为4万元，公务接待决算数为2万元。"
    )
    table = [
        [
            ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
            ["7", "10", "1", "1", "4", "7", "1", "1", "3", "6", "2", "2"],
        ]
    ]

    issues = R33244_Table7_ThreePublicAdvancedCheck().apply(_doc(text, table))

    assert any(issue.rule == "V33-244" for issue in issues)


@pytest.mark.parametrize(
    ("text", "table"),
    [
        (
            "一般公共预算财政拨款“三公”经费支出决算表\n"
            "七、财政拨款三公经费支出决算情况说明。三公经费支出决算数为0万元。",
            [
                [
                    ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
                    ["0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"],
                ]
            ],
        ),
        (
            "一般公共预算财政拨款“三公”经费支出决算表\n"
            "七、财政拨款三公经费支出决算情况说明。三公经费支出决算数为0万元。",
            [
                [
                    ["预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数", "预算数", "决算数"],
                    ["", "", "", "", "", "", "", "", "", "", "", ""],
                ]
            ],
        ),
        (
            "一般公共预算财政拨款“三公”经费支出决算表\n"
            "七、财政拨款三公经费支出决算情况说明。三公经费预算数为7万元，"
            "决算数为10万元，上年决算数为9万元。",
            _three_public_table_row(budget_total="7", final_total="10"),
        ),
        (
            "一般公共预算财政拨款“三公”经费支出决算表\n"
            "七、财政拨款三公经费支出决算情况说明。三公经费支出决算数为10万元。",
            _three_public_table_row(budget_total="7", final_total="10"),
        ),
    ],
)
def test_three_public_matching_or_zero_text_is_not_error(text: str, table: list[list[list[str]]]) -> None:
    issues = R33244_Table7_ThreePublicAdvancedCheck().apply(_doc(text, table))

    assert not any(
        issue.rule == "V33-244" and issue.severity == "error"
        for issue in issues
    )
