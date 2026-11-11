"""R2-01/R2-05：跨表金额原格身份与跨车道编码身份回归。

- R2-01：三公侧证据必须落到金额格/空白零格（source_cell_id），不得退回表名；
  同页多侧也要覆盖。
- R2-05：2025 版经济分类编码判定只看同一语义车道，左栏金额不得参与右栏
  编码拼接；名称末尾缺“费”在精确编码成立时保留金额关系。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.rules_v33 import (  # noqa: E402
    _econ_item_amount_from_lane,
    _econ_table_uses_2025_codes,
)

YICHUAN = (ROOT / "uploads/6000a11c0737d5149975516b44fd8682"
           / "上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf")
SCHOOL = (ROOT / "uploads" / "075e31ba6f282134daad0b9ae225d0a5"
          / "上海科技管理学校2025年度决算.pdf")


def _cell(text=None, number=None):
    if number is not None:
        number = Decimal(str(number))
    return SimpleNamespace(text=text, number=number, page=1)


def test_2025_code_detection_is_lane_scoped_and_ignores_amount():
    """左栏 0.00 是金额，不得与右栏 30209 拼接成编码。"""
    table = SimpleNamespace(
        semantic_columns={"final": [2, 5]},
        rows=[SimpleNamespace(cells=[
            _cell(text="301"), _cell(text="工资福利支出"), _cell(number=0),
            _cell(number=30209), _cell(text="差旅费"), _cell(number=4.15),
        ])],
    )
    assert _econ_table_uses_2025_codes(table) is True

    table_odd_amount = SimpleNamespace(
        semantic_columns={"final": [2, 5]},
        rows=[SimpleNamespace(cells=[
            _cell(text="301"), _cell(text="工资福利支出"), _cell(number=0.01),
            _cell(number=30209), _cell(text="差旅费"), _cell(number=4.15),
        ])],
    )
    # 无关金额改成非整数不应改变版本判断（旧实现在这里翻转）。
    assert _econ_table_uses_2025_codes(table_odd_amount) is True


def test_2025_code_detection_split_code_and_blank_left_amount():
    """拆码 302+09 仍识别；左栏空白金额不得改变结论。"""
    table = SimpleNamespace(
        semantic_columns={"final": [2, 6]},
        rows=[SimpleNamespace(cells=[
            _cell(text="301"), _cell(text="工资福利支出"), _cell(number=None),
            _cell(number=302), _cell(number=9), _cell(text="差旅费"),
            _cell(number=4.15),
        ])],
    )
    assert _econ_table_uses_2025_codes(table) is True


def test_2025_code_detection_requires_code_not_just_name():
    table = SimpleNamespace(
        semantic_columns={"final": [2, 5]},
        rows=[SimpleNamespace(cells=[
            _cell(text="301"), _cell(text="工资福利支出"), _cell(number=0),
            _cell(text="差旅费"), _cell(number=4.15), _cell(number=None),
        ])],
    )
    assert _econ_table_uses_2025_codes(table) is False


def test_2025_code_detection_negative_without_30209():
    table = SimpleNamespace(
        semantic_columns={"final": [2, 5]},
        rows=[SimpleNamespace(cells=[
            _cell(text="301"), _cell(text="工资福利支出"), _cell(number=0),
            _cell(number=30211), _cell(text="差旅费"), _cell(number=4.15),
        ])],
    )
    assert _econ_table_uses_2025_codes(table) is False


def test_suffix_missing_name_keeps_amount_relation_with_exact_code():
    """“公务用车运行维护”漏“费”：精确编码 30229 成立时保留 4.15 金额关系。"""
    cells = [_cell(number=30229), _cell(text="公务用车运行维护"), _cell(number=4.15)]
    hit = _econ_item_amount_from_lane(
        cells, (0, 2), ("公务用车运行维护费",), "30229")
    assert hit is not None
    assert hit["amount"] == Decimal("4.15")
    assert hit["code"] == "30229"
    assert hit["name_suffix_missing"] is True


def _build_real_doc(pdf: Path, org: str):
    import pdfplumber

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_document
    from src.services.pdf_page_extract import (
        extract_tables_from_page,
        extract_visible_text_from_page,
    )
    from src.services.pdf_parser import PDFParser

    artifact = PDFParser(None).build_parse_artifact(str(pdf))
    context = build_fact_context_from_artifact(
        artifact, org_name=org, fiscal_year=2025, report_kind="final",
        doc_type="dept_final")
    with pdfplumber.open(str(pdf)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
        page_tables = [extract_tables_from_page(page) for page in doc_pdf.pages]
    doc = build_document(str(pdf), page_texts, page_tables, pdf.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = context
    return doc, artifact, context, page_texts


@pytest.mark.skipif(not YICHUAN.exists(), reason="宜川原件不在本机")
def test_cross_findings_deliver_amount_cell_or_blank_zero_identity():
    """R2-01：宜川三条跨表问题的三公侧不得只交表名。"""
    import pdfplumber

    from src.engine.pipeline import build_issues_payload
    from src.engine.rule_registry import select_rules
    from src.services.rule_source_binding import bind_rule_sources

    doc, artifact, context, page_texts = _build_real_doc(
        YICHUAN, "上海市普陀区人民政府宜川路街道办事处")
    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind="final",
        rules=list(select_rules("final")))
    result = {"summary": "", "ai_findings": [], "rule_findings": payload["issues"]["all"],
              "merged": {}, "meta": {}}
    bind_rule_sources(
        result, page_texts=page_texts,
        source_file_hash=hashlib.sha256(YICHUAN.read_bytes()).hexdigest(),
        artifact=artifact, fact_context=context,
        consumption=list(payload.get("structured_consumption") or [])
        + list(payload.get("fact_receipts") or []))
    findings = [item for item in result["rule_findings"]
                if item.get("rule_id") == "V33-CROSS-SAN-GONG-ECON"]
    assert len(findings) == 3
    for finding in findings:
        location = finding["location"]
        operands = location.get("relation_operands") or []
        sides = {item.get("side"): item for item in operands}
        assert set(sides) == {"basic", "three_public"}
        for side in ("basic", "three_public"):
            operand = sides[side]
            assert operand["kind"] in {"amount", "blank_zero", "zero_statement"}
            if operand["kind"] == "amount":
                assert operand["source_cell_id"], (finding["id"], side)
                assert operand["value"] == str(
                    location["basic_amount"] if side == "basic"
                    else location["three_public_amount"])
            if operand["kind"] == "blank_zero":
                assert operand["source_cell_id"]
                assert (operand.get("convention") or {}).get("id")
        # 交付证据里必须存在金额原格（blank 侧允许在 native_blank_calculation_refs）
        evidence_cells = {item.get("source_cell_id")
                          for item in finding.get("evidence") or []}
        assert sides["basic"]["source_cell_id"] in evidence_cells
        if sides["three_public"]["kind"] == "amount":
            assert sides["three_public"]["source_cell_id"] in evidence_cells
        else:
            blank_cells = {item.get("source_cell_id") for item in
                           location.get("native_blank_calculation_refs") or []}
            assert sides["three_public"]["source_cell_id"] in blank_cells
        # 表名不得充当任一侧的金额证据
        for item in finding.get("evidence") or []:
            assert "决算表" not in str(item.get("quote") or ""), item


@pytest.mark.skipif(not SCHOOL.exists(), reason="学校原件不在本机")
def test_school_cross_relation_completes_with_lane_identity():
    """R2-05：学校 P11 右栏 30209/30216/30229 与三公表同值关系完成。"""
    from src.engine.pipeline import build_issues_payload, run_rules_with_outcomes
    from src.engine.rule_registry import select_rules

    doc, artifact, context, page_texts = _build_real_doc(
        SCHOOL, "上海科技管理学校")
    payload = build_issues_payload(
        doc, use_ai_assist=False, report_kind="final",
        rules=list(select_rules("final")))
    summary = payload.get("rule_execution_summary") or {}
    assert summary.get("rule_statuses", {}).get("V33-CROSS-SAN-GONG-ECON") in {
        "pass", "fail"}
    unresolved = [item for item in summary.get("unresolved_rules") or []
                  if item.get("rule_id") == "V33-CROSS-SAN-GONG-ECON"]
    assert unresolved == []
    receipts = [item for item in payload.get("fact_receipts") or []
                if item.get("rule_id") == "V33-CROSS-SAN-GONG-ECON"]
    by_check = {item.get("sub_check_id"): item for item in receipts}
    assert by_check["V33-CROSS-SAN-GONG-ECON:cross:san_gong:vehicle_operation"]["status"] == "pass"
    assert "4.15" in str(by_check["V33-CROSS-SAN-GONG-ECON:cross:san_gong:vehicle_operation"]["formula"])
