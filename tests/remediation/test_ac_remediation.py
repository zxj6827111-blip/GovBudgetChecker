"""2026-10-02 独立验收（AC-01…AC-16）反例与修复回归。

每条测试对应验收报告的问题编号，全部为合成输入，不依赖 outputs/ 或
真实盲测材料。修复前这些用例失败；修复后作为正式回归保持。
"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.engine.budget_rules import (
    BUD001_StructureAndAnchors,
    _get_budget_table_rows,
)
from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
from src.engine.fact_context import (
    FactContext,
    FactOverlay,
    apply_fact_overlay,
    build_fact_context_from_artifact,
    parse_overlays_from_records,
)
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import build_document, tolerant_equal
from src.engine.rule_policy import percentage_difference_exceeds_policy
from src.schemas.parse_artifact import (
    ParseArtifact,
    ParsedCell,
    ParsedTable,
    LogicalTableInstance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _make_fragment(
    code: str,
    rows: List[List[str]],
    *,
    title: str = "测试表",
    page: int = 1,
    unit_hint: str = "万元",
    confidence: float = 0.95,
) -> ParsedTable:
    cells = [
        ParsedCell(
            row_idx=r,
            col_idx=c,
            raw_text=rows[r][c],
            normalized_text=rows[r][c],
            numeric_value=(
                float(rows[r][c].replace(",", ""))
                if rows[r][c].replace(".", "").replace(",", "").replace("-", "").isdigit()
                else None
            ),
            page_number=page,
            bbox=(10.0 * r, 10.0 * c, 10.0 * r + 5, 10.0 * c + 5),
            is_header=(r == 0),
            confidence=confidence,
            unit_hint=unit_hint,
            extraction_method="lines",
        )
        for r in range(len(rows))
        for c in range(len(rows[r]))
    ]
    return ParsedTable(
        table_code=code,
        title=title,
        page_number=page,
        table_index=0,
        bbox=(0, 0, 100, 100),
        header_row_count=1,
        confidence=confidence,
        extraction_method="lines",
        unit_hint=unit_hint,
        rows=rows,
        cells=cells,
    )


def _artifact_with(fragment: ParsedTable, code: str, title: str) -> ParseArtifact:
    instance = LogicalTableInstance(
        instance_uid=fragment.fragment_uid,
        table_code=code,
        title=title,
        fragment_uids=[fragment.fragment_uid],
        page_start=fragment.page_number,
        page_end=fragment.page_number,
        column_mappings=[],
    )
    return ParseArtifact(
        source_file_hash="abc123",
        page_count=1,
        table_fragments=[fragment],
        logical_tables=[instance],
    )


def _run_one(doc: Any, rule: Any) -> Dict[str, Any]:
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[rule])
    return {
        "outcomes": [o.to_dict() for o in outcomes],
        "issues": [i for i in issues],
        "receipts": list(getattr(doc, "fact_receipts", []) or []),
    }


# ---------------------------------------------------------------------------
# AC-01 统一事实进入主规则链
# ---------------------------------------------------------------------------


def test_ac01_fact_context_builds_from_artifact_without_database() -> None:
    """规则执行前从统一解析对象构建事实上下文（纯函数、无数据库）。"""
    fragment = _make_fragment(
        "FIN_03_expenditure",
        [["项目", "合计", "基本支出", "项目支出"], ["教育", "100.00", "60.00", "40.00"]],
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_03_expenditure", "支出决算表"),
        org_name="测试局",
        fiscal_year=2026,
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    summary = context.summary()
    assert summary["fact_count"] >= 3
    assert summary["operand_count"] == summary["fact_count"]
    value, fact = context.best_value("total_actual")
    assert value == Decimal("100.0")
    assert fact is not None and fact.fact_uid


def test_ac01_low_confidence_facts_are_not_definite_operands() -> None:
    """识别状态 ambiguous/low_confidence 的事实不能作为确定操作数。"""
    fragment = _make_fragment(
        "FIN_03_expenditure",
        [["项目", "合计", "基本支出", "项目支出"], ["教育", "100.00", "60.00", "40.00"]],
        confidence=0.5,
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_03_expenditure", "支出决算表"),
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    assert context.definite_facts() == []
    assert context.non_operand_facts()
    value, fact = context.best_value("total_actual")
    assert value is None and fact is not None


def test_ac01_document_carries_fact_context_into_rules() -> None:
    """Document.fact_context 挂载后，规则侧可取到事实操作数（BUD-107 消费）。"""
    fragment = _make_fragment(
        "FIN_01_income_expenditure_total",
        [
            ["项目", "本年收入", "本年支出"],
            ["合计", "328661.81", "328661.81"],
        ],
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_01_income_expenditure_total", "收支总表"),
        org_name="测试局",
        fiscal_year=2026,
        report_kind="budget",
        doc_type="budget",
    )
    doc = build_document(
        "budget.pdf",
        ["2026年部门预算收支总表\n单位：万元\n收入预算328661.81万元，支出预算328661.81万元。"],
        [[
            ["项目", "收入预算", "支出预算"],
            ["收入总计", "328661.81", "328661.81"],
            ["支出总计", "", ""],
        ]],
        0,
    )
    doc.fact_context = context
    from src.engine.budget_rules import BUD107_TextTableConsistency

    output = _run_one(doc, BUD107_TextTableConsistency())
    fact_aware = any(
        receipt.get("evaluation_basis") == "fiscal_fact"
        for receipt in output["receipts"]
    )
    assert fact_aware, f"BUD-107 必须写事实口径回执：{output['receipts'][:2]}"


# ---------------------------------------------------------------------------
# AC-02 表身份
# ---------------------------------------------------------------------------


def test_ac02_t9_binds_three_public_table_not_state_capital_on_same_page() -> None:
    """同页放三公表与国资表：T9 必须绑三公表、T7 绑国资表（签名错位反例）。"""
    san_gong = [
        ["项目", "预算数"],
        ["合计", "30.00"],
        ["因公出国（境）费", "0.00"],
        ["公务接待费", "10.00"],
        ["机关运行经费", "50.00"],
    ]
    state_capital = [
        ["项目", "预算数"],
        ["合计", "999.00"],
        ["国有资本经营预算支出", "999.00"],
    ]
    doc = build_document("budget.pdf", ["2026年部门预算", "同页两表"], [[san_gong, state_capital]], 0)
    t9_rows, _ = _get_budget_table_rows(doc, {"BUD_T9": [1]}, "BUD_T9", include_continuation=False)
    assert t9_rows == san_gong, "T9 不得按旧签名词取到国资表的合计"
    t7_rows, _ = _get_budget_table_rows(doc, {"BUD_T7": [1]}, "BUD_T7", include_continuation=False)
    assert t7_rows == state_capital, "T7 应绑定国资表"


def test_ac02_conditional_tables_unknown_state_not_reported_missing() -> None:
    """条件性表：材料无基金/国资内容时不判缺表；确有基金内容缺表仍要检出。"""
    titles = [
        "部门预算收支总表",
        "部门收入预算总表",
        "部门支出预算总表",
        "部门财政拨款收支预算总表",
        "部门一般公共预算支出功能分类预算表",
        "部门一般公共预算基本支出经济分类预算表",
        "部门“三公”经费和机关运行经费预算表",
    ]
    doc = build_document("budget.pdf", titles, [[] for _ in titles], 0)
    output = _run_one(doc, BUD001_StructureAndAnchors())
    conditional_hits = [
        issue
        for issue in output["issues"]
        if any(key in issue.message for key in ("BUD_T6", "BUD_T7"))
    ]
    assert not conditional_hits, "unknown 状态不得直接判条件性表缺失"

    # 确有基金预算内容但缺表：必须检出（正例）
    doc_fund = build_document(
        "budget.pdf",
        [*titles, "2026年安排政府性基金预算拨款150万元，用于专项项目。"],
        [[] for _ in [*titles, "extra"]],
        0,
    )
    output_fund = _run_one(doc_fund, BUD001_StructureAndAnchors())
    assert any("BUD_T6" in issue.message for issue in output_fund["issues"]), (
        "确有政府性基金预算内容而缺表必须报缺失"
    )


def test_ac02_parser_does_not_merge_same_code_fragments_with_different_titles() -> None:
    """同表种不同表题（不同主体/年度）的相邻页片段不得合并为同一实例。"""
    from src.services.pdf_parser import PDFParser

    parser = PDFParser(None)

    def frag(title: str, page: int, row_label: str) -> ParsedTable:
        rows = [["项目", "金额"], [row_label, "100.00"]]
        cells = [
            ParsedCell(
                row_idx=r, col_idx=c, raw_text=rows[r][c], normalized_text=rows[r][c],
                numeric_value=(100.0 if r == 1 and c == 1 else None),
                page_number=page, bbox=None, is_header=(r == 0),
                confidence=0.9, unit_hint=None, extraction_method="lines",
            )
            for r in range(2) for c in range(2)
        ]
        return ParsedTable(
            table_code="FIN_02_income", title=title, page_number=page, table_index=0,
            bbox=(0, 0, 100, 100), header_row_count=1, confidence=0.9,
            extraction_method="lines", unit_hint=None, rows=rows, cells=cells,
        )

    merged = parser._coalesce_tables(
        [frag("甲局2024收入表", 1, "甲收入"), frag("乙局2025收入表", 2, "乙收入")]
    )
    assert len(merged) == 2, "不同主体/年度的同种表是两个实例"

    # 重复表题的真实续页仍合并，且实例页跨度正确、来源片段保留
    merged2 = parser._coalesce_tables(
        [frag("收入决算表", 1, "甲收入"), frag("收入决算表", 2, "甲收入续")]
    )
    assert len(merged2) == 1
    instance = parser._build_logical_instance(merged2[0])
    assert instance.page_end == 2, "逻辑实例 page_end 必须取单元格真实物理页"
    assert instance.fragment_uids == merged2[0].source_fragment_uids + [merged2[0].fragment_uid]


# ---------------------------------------------------------------------------
# AC-04 关系级回执
# ---------------------------------------------------------------------------


def test_ac04_cmm001_writes_relation_receipts_and_defers_partial() -> None:
    """三公只披露合计：合计可比对但分项关系全部未完成 → 转未完成+回执。"""
    text = "2026年部门“三公”经费和机关运行经费预算表\n单位：万元\n三公经费预算30.00万元。"
    table = [
        ["合计", "因公出国（境）费", "公务用车购置及运行费", "购置费", "运行费", "公务接待费", "机关运行经费"],
        ["30.00", "0.00", "20.00", "0.00", "20.00", "10.00", "50.00"],
    ]
    doc = build_document("budget.pdf", [text], [[table]], 0)
    output = _run_one(doc, CMM001_ThreePublicNarrativeConsistency())
    statuses = [outcome["status"] for outcome in output["outcomes"]]
    assert "pass" not in statuses, "分项关系未完成时不得整项 pass"
    assert output["receipts"], "必须产生关系级子检查回执"
    for receipt in output["receipts"]:
        assert receipt["formula"], "回执必须携带公式"
        assert "tolerance" in receipt and receipt["tolerance"], "回执必须记录容差"


# ---------------------------------------------------------------------------
# AC-06 容差统一
# ---------------------------------------------------------------------------


def test_ac06_portable_tolerance_boundaries_in_production_rules() -> None:
    """0.5 万元边界内外与 0.1 个百分点边界内外——生产比较统一政策口径。"""
    assert tolerant_equal(1000.0, 1000.5) is True, "0.5 边界内允许"
    assert tolerant_equal(1000.0, 1000.51) is False, "超过 0.5 必须报（旧相对容差漏报）"
    assert tolerant_equal(1000.0, 1000.6) is False, "V33-103 的 1000 vs 1000.60 反例"
    assert tolerant_equal(100.0, 100.10) is True, "旧 0.01 局部容差把舍入当错误"
    assert tolerant_equal(100.0, 100.50) is True
    assert tolerant_equal(100.0, 100.51) is False
    assert percentage_difference_exceeds_policy("0.1") is False
    assert percentage_difference_exceeds_policy("0.1001") is True


# ---------------------------------------------------------------------------
# AC-08 证据按来源列绑定 + 完整证据实体
# ---------------------------------------------------------------------------


def test_ac08_equal_amounts_in_different_columns_get_distinct_evidence() -> None:
    """同额不同列：两事实的证据单元格必须不同，且证据携带完整定位实体。"""
    fragment = _make_fragment(
        "FIN_03_expenditure",
        [["项目", "合计", "基本支出", "项目支出"], ["教育", "100.00", "100.00", ""]],
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_03_expenditure", "支出决算表"),
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    by_indicator: Dict[str, List[str]] = {}
    for fact in context.facts:
        refs = fact.extra_dims.get("evidence_refs") or []
        assert refs, "事实必须携带完整证据实体（evidence_refs）"
        ref = refs[0]
        assert ref["physical_page"] == 1
        assert ref["bbox"], "证据必须携带 bbox"
        assert ref["file_hash"] == "abc123"
        assert ref["source_cell_id"]
        by_indicator.setdefault(fact.indicator, []).append(ref["source_cell_id"])
    assert by_indicator["total_actual"] != by_indicator["basic_actual"], (
        "总额与基本支出同额不同列不得绑定同一证据"
    )


# ---------------------------------------------------------------------------
# AC-10 校正覆盖层
# ---------------------------------------------------------------------------


def _correction_record(fact_uid: str, value: str) -> Dict[str, Any]:
    return {
        "record_id": "cor-test",
        "analysis_basis_token": "job:rev-1",
        "actor": "reviewer",
        "reason": "人工复核原始表格后修正",
        "changes": [
            {
                "target_type": "fact",
                "target_id": fact_uid,
                "field": "raw_value",
                "value": value,
                "evidence_ids": ["ev-1"],
            }
        ],
    }


def test_ac10_overlay_changes_fact_values_and_version_token() -> None:
    """人工校正通过覆盖层改变事实值：新版本令牌 + 状态 verified + 原值留痕。"""
    fragment = _make_fragment(
        "FIN_03_expenditure",
        [["项目", "合计", "基本支出", "项目支出"], ["教育", "100.00", "60.00", "40.00"]],
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_03_expenditure", "支出决算表"),
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    value, fact = context.best_value("total_actual")
    assert fact is not None

    overlays = parse_overlays_from_records([_correction_record(fact.fact_uid, "90.00")])
    assert len(overlays) == 1
    new_context = apply_fact_overlay(context, overlays)
    new_value, _ = new_context.best_value("total_actual")
    assert new_value == Decimal("90.0"), "校正后事实值必须变化（重算输入随之变化）"
    assert new_context.version_token != context.version_token, "事实版本令牌必须不同"
    assert new_context.facts[0].recognition_status == "verified"
    assert new_context.facts[0].extra_dims["pre_correction_values"][0]["normalized_value"] == "100.0"


def test_ac10_overlay_rejects_unknown_fact_target() -> None:
    """校正目标必须存在于指定事实版本——未知 fact_uid 拒绝。"""
    fragment = _make_fragment(
        "FIN_03_expenditure",
        [["项目", "合计", "基本支出", "项目支出"], ["教育", "100.00", "60.00", "40.00"]],
    )
    context = build_fact_context_from_artifact(
        _artifact_with(fragment, "FIN_03_expenditure", "支出决算表"),
        report_kind="final",
        doc_type="final",
    )
    assert context is not None
    overlays = parse_overlays_from_records([_correction_record("fact-not-exist", "1.00")])
    with pytest.raises(ValueError):
        apply_fact_overlay(context, overlays)


def test_ac10_correction_overlay_flows_into_reanalysis_pipeline_block() -> None:
    """主链重分析场景：corrections.jsonl 中的改值记录可解析为覆盖层。"""
    record = _correction_record("some-fact", "90.00")
    overlays = parse_overlays_from_records([record])
    assert overlays and overlays[0].fact_uid == "some-fact"
    assert overlays[0].raw_value == "90.00"
