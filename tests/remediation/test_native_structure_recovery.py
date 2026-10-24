"""真实原件字符/网格快照回归；期望的 19 个金额与科目由原页核对确定。"""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.schemas.parse_artifact import ParseArtifact, ParsedTable
from src.services.native_structure_recovery import repair_native_structure
from src.services.pdf_parser import PDFParser
from src.services.table_reading_assessment import READING_VERSION, assess_table_reading

FIXTURES = Path(__file__).parents[1] / "fixtures/native_structure"
EXPECTED = {
    "open_fiscal_tail": [
        (None, "十七、援助其他地区支出", measure, "0.00")
        for measure in (
            "expenditure_actual",
            "general_public_budget",
            "government_fund_budget",
            "state_capital_budget",
        )
    ],
    "open_basic_tail": [("31022", "无形资产购置", "total_actual", "0.00")],
    "missing_money_grid": [
        ("total", "合计", measure, amount)
        for measure, amount in (
            ("total_actual", "650.58"),
            ("basic_actual", "374.00"),
            ("project_actual", "276.58"),
        )
    ],
    "decimal_wrap": [
        (code, name, "income_actual", amount)
        for code, name, amount in (
            (None, "五、事业收入", "13,774.36"),
            ("total", "本年收入合计", "18,331.49"),
            ("total", "总计", "21,362.29"),
        )
    ],
    "merged_rows": [
        (code, name, measure, amount)
        for code, name, amount in (
            ("2080501", "行政单位离退休", "38.24"),
            ("2080502", "事业单位离退休", "85.93"),
            ("2080505", "机关事业单位基本养老保险缴费支出", "213.13"),
            ("2080506", "机关事业单位职业年金缴费支出", "113.51"),
        )
        for measure in ("total_actual", "basic_actual")
    ],
}


def fixture(kind):
    data = json.loads((FIXTURES / f"{kind}.json").read_text())
    return SimpleNamespace(**data["page"]), ParsedTable.from_dict(data["table"]), data


def artifact(table, data):
    return ParseArtifact(
        source_file_hash=data["source"]["sha256"],
        page_count=data["source"]["physical_page"],
        table_fragments=[table],
        physical_fragments=[deepcopy(table)],
        logical_tables=[PDFParser(None)._build_logical_instance(table)],
        reading_assessment={"version": READING_VERSION, "pages": [data["probe"]]},
    )


@pytest.mark.parametrize("kind", EXPECTED)
def test_original_cases_recover_exact_amount_and_subject_and_keep_character_proof(kind):
    page, table, data = fixture(kind)
    original = table.to_dict()
    fixed = repair_native_structure(page, table)
    assert table.to_dict() == original  # 原始网格不被就地覆盖。
    a = artifact(fixed, data)
    assert ParseArtifact.from_dict(a.to_dict()).to_dict() == a.to_dict()
    assert assess_table_reading(a)["gaps"] == []
    context = build_fact_context_from_artifact(a, report_kind="final")
    assert context and context.facts
    for code, name, indicator, amount in EXPECTED[kind]:
        matches = [
            f
            for f in context.facts
            if f.subject_code == code
            and "".join((f.subject_name or "").split()) == name
            and f.indicator == indicator
        ]
        assert len(matches) == 1, (kind, code, name, indicator)
        fact = matches[0]
        assert fact.raw_value == amount and fact.normalized_value == Decimal(
            amount.replace(",", "")
        )
        assert operand_rejection_reason(fact) is None
        ref = fact.extra_dims["evidence_refs"][0]
        assert ref["quote"] == amount and ref["physical_page"] == data["source"]["physical_page"]
        assert ref["source_provenance"]["native_text_sources"]
    for cell in fixed.cells:
        sources = [s for s in cell.native_text_sources if s.get("recovery_kind")]
        if any(s["recovery_kind"] == "open_grid_tail" for s in sources):
            assert cell.grid_raw_text is None
        for source in sources:
            chars = page.chars[source["start_char_index"] : source["end_char_index"]]
            assert "".join(c["text"] for c in chars) == source["text"]
            assert source["bbox"] == [
                min(c["x0"] for c in chars),
                min(c["top"] for c in chars),
                max(c["x1"] for c in chars),
                max(c["bottom"] for c in chars),
            ]
    assert repair_native_structure(page, fixed).to_dict() == fixed.to_dict()


def test_complete_amounts_in_two_lines_are_never_concatenated():
    page, table, _ = fixture("decimal_wrap")
    cell = next(c for c in table.cells if c.raw_text == "13,774.3\n6")
    cell.raw_text = "13,774.30\n6.00"
    fixed = repair_native_structure(page, table)
    result = next(c for c in fixed.cells if (c.row_idx, c.col_idx) == (cell.row_idx, cell.col_idx))
    assert result.raw_text == cell.raw_text and result.numeric_value is None


def test_decimal_tail_at_another_horizontal_position_is_not_joined():
    page, table, _ = fixture("decimal_wrap")
    cell = next(c for c in table.cells if c.raw_text == "13,774.3\n6")
    digit = next(
        c
        for c in page.chars
        if c["text"] == "6"
        and cell.bbox[0] <= c["x0"] < cell.bbox[2]
        and cell.bbox[1] <= c["top"] < cell.bbox[3]
    )
    digit["x0"] -= 20
    digit["x1"] -= 20
    fixed = repair_native_structure(page, table)
    assert (
        next(c for c in fixed.cells if c.row_idx == cell.row_idx and c.col_idx == 1).numeric_value
        is None
    )


def test_decimal_recovery_requires_contiguous_original_character_indexes():
    page, table, _ = fixture("decimal_wrap")
    position = next(i for i, c in enumerate(page.chars) if c["text"] == "1" and c["x0"] == 253.44)
    page.chars.insert(position + 2, {**page.chars[position + 1], "text": " "})
    fixed = repair_native_structure(page, table)
    assert next(c for c in fixed.cells if c.raw_text == "13,774.3\n6").numeric_value is None


def test_missing_amount_in_a_merged_group_prevents_partial_row_reassignment():
    page, table, _ = fixture("merged_rows")
    amount = next(c for c in table.cells if c.col_idx == 2 and c.raw_text.startswith("38.24\n"))
    page.chars = [
        c
        for c in page.chars
        if not (amount.bbox[0] <= c["x0"] < amount.bbox[2] and 417 < c["top"] < 430)
    ]
    fixed = repair_native_structure(page, table)
    assert len(fixed.rows) == len(table.rows)
    assert fixed.rows[-1][0] == "2080501\n2080502\n2080505\n2080506"


def test_money_without_an_original_label_is_not_given_a_subject():
    page, table, _ = fixture("missing_money_grid")
    for c in table.cells:
        if c.row_idx == len(table.rows) - 1 and c.col_idx in (0, 1):
            c.raw_text = ""
    fixed = repair_native_structure(page, table)
    assert all(c.numeric_value is None for c in fixed.cells if c.row_idx == len(table.rows) - 1)


def test_separated_horizontal_integers_cannot_turn_into_one_amount():
    page, table, _ = fixture("missing_money_grid")
    page.chars = [c for c in page.chars if not (280 < c["x0"] < 330 and 415 < c["top"] < 430)]
    for x, digit in [(275, "1"), (280, "0"), (310, "2"), (315, "0")]:
        page.chars.append(dict(text=digit, x0=x, x1=x + 5, top=416.11, bottom=427.16, upright=True))
    fixed = repair_native_structure(page, table)
    assert not any(c.numeric_value == 1020 for c in fixed.cells)
    assert (
        next(
            c for c in fixed.cells if c.row_idx == len(table.rows) - 1 and c.col_idx == 2
        ).numeric_value
        is None
    )


@pytest.mark.parametrize("kind", ["open_fiscal_tail", "open_basic_tail"])
def test_open_tail_without_label_cannot_be_attached_as_a_data_row(kind):
    page, table, _ = fixture(kind)
    last_amount_left = max(
        c.bbox[0] for c in table.cells if c.col_idx == len(table.rows[-1]) - 1 and c.bbox
    )
    page.chars = [c for c in page.chars if c["top"] < table.bbox[3] or c["x0"] >= last_amount_left]
    fixed = repair_native_structure(page, table)
    assert fixed.to_dict() == table.to_dict()


@pytest.mark.parametrize("kind", EXPECTED)
def test_overlapping_image_does_not_certify_native_recovery(kind):
    page, table, _ = fixture(kind)
    page.images = [
        dict(x0=table.bbox[0], top=table.bbox[1], x1=table.bbox[2], bottom=table.bbox[3] + 50)
    ]
    assert repair_native_structure(page, table).to_dict() == table.to_dict()


def test_tail_label_continuation_outside_initial_band_is_not_truncated():
    page, table, _ = fixture("open_basic_tail")
    page.chars.append(dict(text="续", x0=349.32, x1=356.77, top=760, bottom=767.45, upright=True))
    assert repair_native_structure(page, table).to_dict() == table.to_dict()


def test_name_not_centered_on_its_code_prevents_merged_row_reassignment():
    page, table, _ = fixture("merged_rows")
    for char in page.chars:
        if 150 < char["x0"] < 280 and 395 < char["top"] < 398:
            char["top"] += 7
            char["bottom"] += 7
    assert repair_native_structure(page, table).to_dict() == table.to_dict()


def test_partial_decimal_and_integer_with_two_subject_lines_are_not_joined():
    page, table, _ = fixture("decimal_wrap")
    amount = next(c for c in table.cells if c.raw_text == "13,774.3\n6")
    label = next(c for c in table.cells if c.row_idx == amount.row_idx and c.col_idx == 0)
    page.chars = [
        c
        for c in page.chars
        if not (
            label.bbox[0] <= c["x0"] < label.bbox[2] and label.bbox[1] <= c["top"] < label.bbox[3]
        )
    ]
    for y, word in [(234.1, "事业收入"), (249.7, "其他收入")]:
        for i, char in enumerate(word):
            page.chars.append(
                dict(
                    text=char,
                    x0=68 + i * 10.45,
                    x1=78.45 + i * 10.45,
                    top=y,
                    bottom=y + 10.45,
                    upright=True,
                )
            )
    label.raw_text = "事业收入\n其他收入"
    fixed = repair_native_structure(page, table)
    assert (
        next(c for c in fixed.cells if c.row_idx == amount.row_idx and c.col_idx == 1).numeric_value
        is None
    )


def test_partial_amount_aligned_to_only_one_subject_line_is_not_joined():
    page, table, _ = fixture("decimal_wrap")
    amount = next(c for c in table.cells if c.raw_text == "13,774.3\n6")
    label = next(c for c in table.cells if c.row_idx == amount.row_idx and c.col_idx == 0)
    for char in page.chars:
        if (
            label.bbox[0] <= char["x0"] < label.bbox[2]
            and label.bbox[1] <= char["top"] < label.bbox[3]
        ):
            char["top"] -= 8
            char["bottom"] -= 8
    fixed = repair_native_structure(page, table)
    assert (
        next(c for c in fixed.cells if c.row_idx == amount.row_idx and c.col_idx == 1).numeric_value
        is None
    )
