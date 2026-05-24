import os

os.environ.setdefault("GOVBUDGET_AUTH_ENABLED", "false")

from api import runtime


def test_parse_report_year_supports_two_digit_year_hint() -> None:
    assert runtime.parse_report_year("25 budget") == 2025
    assert runtime.parse_report_year("office-24 final.pdf") == 2024


def test_infer_report_year_prefers_filename_and_budget_lines() -> None:
    page_texts = [
        "\n".join(
            [
                "Office report 2025 budget",
                "1. 2025 department budget total table",
                "Context may mention 2026 planning notes",
            ]
        ),
        "Table of contents\n2025 department income budget total",
    ]

    year = runtime.infer_report_year(
        filename="office-25-budget.pdf",
        page_texts=page_texts,
        preferred_year=2026,
    )

    assert year == 2025


def test_infer_report_year_prefers_cover_title_when_filename_is_short_form() -> None:
    page_texts = [
        "\n".join(
            [
                "上海市普陀区2026年区级单位预算",
                "预算单位：上海市普陀区人民政府石泉路街道办事处（本级）",
            ]
        )
    ]

    year = runtime.infer_report_year(
        filename="石泉26单位.pdf",
        page_texts=page_texts,
        preferred_year=None,
    )
    cover = runtime.extract_cover_metadata(
        page_texts=page_texts,
        filename="石泉26单位.pdf",
        preferred_year=None,
        doc_type=None,
    )

    assert year == 2026
    assert cover["report_year"] == 2026
    assert cover["report_kind"] == "budget"
    assert cover["scope_hint"] == "unit"
    assert cover["cover_org_label"] == "预算单位"
