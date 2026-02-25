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
