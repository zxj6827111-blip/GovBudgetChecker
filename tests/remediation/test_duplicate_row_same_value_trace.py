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

import json
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


# ---------------------------------------------------------------------------
# C5 收口（第二轮复验）：合并证明必须进入**持久回执**（disclosure_sources），
# 经 select→receipt→JSON 序列化→API 读回全链可回溯双侧；不能只断言内存
# extra_dims。重复调用幂等去重；异值/冲突不收敛。
# ---------------------------------------------------------------------------

def _grid_doc(rows):
    from decimal import Decimal as _D

    from src.engine.fact_context import build_fact_context_from_artifact
    from src.engine.pipeline import build_document
    from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable

    cells = [
        ParsedCell(r, c, str(v) if v is not None else '', str(v) if v is not None else '',
                   _D(str(v)) if (r >= 2 and c >= 2 and v) else None, 1,
                   (c * 70, r * 25, (c + 1) * 70, (r + 1) * 25), r < 2, .99, '万元', 'lines')
        for r, row in enumerate(rows) for c, v in enumerate(row)
    ]
    table = ParsedTable('FIN_03_expenditure', '支出决算表', 1, 0,
                        (0, 0, 560, 25 * len(rows)), 2, .99, 'lines', '万元', rows, cells, 'frag')
    instance = LogicalTableInstance('instance', 'FIN_03_expenditure', table.title,
                                    ['frag'], 1, 1, org_name=ORG, fiscal_year=YEAR)
    artifact = ParseArtifact(source_file_hash='a' * 64, parse_run_uid='run',
                             table_fragments=[table], physical_fragments=[table],
                             logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name=ORG, fiscal_year=YEAR,
                                               report_kind='final', doc_type='dept_final')
    doc = build_document('dup.pdf', [f'{ORG}2025年度部门决算'], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    doc.fact_receipts = []
    return doc


DUP_ROWS = [
    ['功能分类科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出', '上缴上级支出', '经营支出', '对附属单位补助支出'],
    ['功能分类科目编码', '科目名称', None, None, None, None, None, None],
    ['合计', None, '196.51', '100.00', '96.51', '', '', ''],
    ['201', '一般公共服务支出', '196.51', '100.00', '96.51', '', '', ''],
    ['20123', '民族事务', '96.51', '50.00', '46.51', '', '', ''],
    ['20123', '民族事务', '96.51', '50.00', '46.51', '', '', ''],
    ['2012301', '行政运行', '50.00', '50.00', '0.00', '', '', ''],
    ['2012304', '民族工作专项', '46.51', '0.00', '46.51', '', '', ''],
]


def _duplicate_sources(receipt):
    return [d for d in (receipt.get('disclosure_sources') or [])
            if d.get('role') == 'same_value_duplicate_row']


def test_duplicate_merge_proof_persists_to_final_relation_receipt():
    """V33-005 真实规则：PASS 关系回执携带双侧合并证明（select→receipt）。"""
    from src.engine.rules_v33 import R33005_TableTotalConsistency

    doc = _grid_doc(DUP_ROWS)
    issues = R33005_TableTotalConsistency().apply(doc)
    assert isinstance(issues, list)
    receipt = next(r for r in doc.fact_receipts
                   if r.get('sub_check_id') == 'FIN_03_expenditure:20123:total_actual')
    assert receipt['status'] == 'pass'
    dupes = _duplicate_sources(receipt)
    assert dupes, "合并证明必须随最终关系回执持久交付"
    trace = dupes[0]
    assert trace['duplicate_fact_uid'] and trace['kept_fact_uid']
    assert trace['duplicate_fact_uid'] != trace['kept_fact_uid']
    assert trace['source_cell_id'] and trace['physical_page']
    assert trace['quote'] == '96.51' and trace['value'] == '96.51'
    assert trace['file_hash'] == 'a' * 64
    assert trace['parse_run_uid'] == 'run'
    assert trace['basis'] == 'same_object_same_value_duplicate_row'


def test_duplicate_merge_proof_survives_json_roundtrip():
    """序列化/API 读回等价：回执经 JSON roundtrip 后双侧证据逐字保持。"""
    from src.engine.rules_v33 import R33005_TableTotalConsistency

    doc = _grid_doc(DUP_ROWS)
    R33005_TableTotalConsistency().apply(doc)
    receipt = next(r for r in doc.fact_receipts
                   if r.get('sub_check_id') == 'FIN_03_expenditure:20123:total_actual')
    roundtrip = json.loads(json.dumps(receipt, ensure_ascii=False, default=str))
    dupes = [d for d in (roundtrip.get('disclosure_sources') or [])
             if d.get('role') == 'same_value_duplicate_row']
    assert len(dupes) == 1 and dupes[0]['duplicate_fact_uid']
    assert dupes[0]['quote'] == '96.51'


def test_duplicate_merge_trace_idempotent_across_repeated_selects():
    """同一关系重复调用 select：合并留痕幂等去重，不重复追加。"""
    first = _fact("2012304", "民族工作专项", "12.34", cell_id="frag:r10c2", page=8)
    second = _fact("2012304", "民族工作专项", "12.34", cell_id="frag:r11c2", page=9)
    doc = _doc([first, second])
    for _ in range(3):
        fact, error = select(doc, [first, second], "total_actual", code="2012304")
        assert error is None and fact is first
    traces = first.extra_dims.get('same_value_duplicate_cells') or []
    assert len(traces) == 1, "重复调用不得重复追加合并留痕"
    from src.engine.native_table_arithmetic import receipt as _receipt

    class _Rule:
        code = 'TEST'

    _receipt(_Rule(), doc, 'test:relation', [first], status='pass', formula='test')
    receipt_row = next(r for r in doc.fact_receipts if r.get('sub_check_id') == 'test:relation')
    assert len(_duplicate_sources(receipt_row)) == 1
