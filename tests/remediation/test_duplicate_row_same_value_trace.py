"""C5（2026-11-14 复验）：重复行合并的边界——同值留双侧证据，异值拒绝。

真实材料（民宗 410b4a3d…）：原件第8、9页确实重复印出 201/20123/2012301/
2012304/208 等行。重复行 finding 由 V33-005 单独保留；涉及重复行空白格
唯一性的义务阻断如实保留（真实原件歧义，人工复核），不强求归零。

本文件锁定 select() 收敛的两个边界：
- 同码同值（身份+金额+完整来源证明同一对象重复）：合并为同一业务行参与
  计算，但被合并的重复**原格证据**随保留事实留痕（双侧可回原件）；
- 同码异值：不收敛，保持"操作数不唯一"拒绝。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_table_arithmetic import select
from src.schemas.parse_artifact import EvidenceRef, FiscalFact

ORG = "甲区民族和宗教事务办公室"
YEAR = 2025


def _fact(code, name, raw, *, cell_id, page=8):
    return FiscalFact(
        fact_uid=f"fact-{code}-{cell_id}", indicator="total_actual", raw_value=raw,
        unit="万元", normalized_value=Decimal(raw.replace(",", "")),
        org_name=ORG, org_scope="单位", fiscal_year=YEAR, period="本年",
        fund_scope="general_public_budget", budget_stage="决算",
        subject_code=code, subject_name=name,
        table_code="FIN_03_expenditure", instance_uid="instance-1",
        extra_dims={"evidence_refs": [EvidenceRef(
            file_hash="a" * 64, physical_page=page, source_cell_id=cell_id,
            quote=raw, location_level="exact_cell").to_dict()]},
    )


def _doc(facts):
    class _Artifact:
        source_file_hash = "a" * 64
        parse_run_uid = "run"

    class _Context(FactContext):
        pass

    context = _Context(facts, "a" * 64, "run", org_name=ORG, fiscal_year=YEAR,
                       report_kind="final")

    class _Doc:
        fact_context = context
        parse_artifact = _Artifact()
        page_texts = ["原表"]

    return _Doc()


def test_same_value_duplicate_rows_merge_and_keep_both_cell_proofs():
    """同码同值重复行：合并计算，双侧原格证据随保留事实留痕。"""
    first = _fact("2012304", "民族工作专项", "12.34", cell_id="frag:r10c2", page=8)
    second = _fact("2012304", "民族工作专项", "12.34", cell_id="frag:r11c2", page=9)
    fact, error = select(_doc([first, second]), [first, second], "total_actual",
                         code="2012304")
    assert error is None and fact is first
    traces = fact.extra_dims.get("same_value_duplicate_cells") or []
    assert len(traces) == 1, "被合并的重复原格必须留痕"
    trace = traces[0]
    assert trace["source_cell_id"] == "frag:r11c2", "留痕是另一侧的原格身份"
    assert trace["physical_page"] == 9 and trace["quote"] == "12.34"
    assert trace["basis"] == "same_object_same_value_duplicate_row"


def test_different_value_duplicate_rows_stay_rejected():
    """同码异值重复行：不收敛，保持操作数不唯一拒绝。"""
    first = _fact("2012304", "民族工作专项", "12.34", cell_id="frag:r10c2", page=8)
    second = _fact("2012304", "民族工作专项", "56.78", cell_id="frag:r11c2", page=9)
    fact, error = select(_doc([first, second]), [first, second], "total_actual",
                         code="2012304")
    assert fact is None and error and "不唯一" in error


def test_merge_trace_records_do_not_mutate_amounts():
    """留痕只记录原件格身份，不改写金额与归一值。"""
    first = _fact("201", "一般公共服务支出", "100.00", cell_id="frag:r1c2", page=8)
    second = _fact("201", "一般公共服务支出", "100.00", cell_id="frag:r2c2", page=9)
    before = (first.raw_value, first.normalized_value, first.subject_name)
    fact, error = select(_doc([first, second]), [first, second], "total_actual", code="201")
    assert error is None
    assert (fact.raw_value, fact.normalized_value, fact.subject_name) == before
    assert fact.extra_dims.get("evidence_refs"), "保留事实自身证据引用不变"
