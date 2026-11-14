"""D2b 一般公共预算路径：同文独立原表交叉证据绑定（表内中间漏字）。

背景（2026-11-12 民政批次诊断·社会福利院）：FIN_05 表内 2080502 名称
「事业单离退休」（原件漏"位"字），说明写「事业单位离退休」48.74 万元。
同文收入/支出决算表（FIN_02/FIN_03）同编码均印完整名称。旧实现候选为空，
V33-222 金额关系被阻断；新实现在"其他原表打印编码+完整名称唯一锚定编码、
目标表内按编码唯一定位"时绑定金额，名称差异仍由 V33-227 独立报告。
"""

from decimal import Decimal

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import _hierarchy_fact
from src.schemas.parse_artifact import (
    EvidenceRef,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)
from tests.remediation.test_r22_a2_identity_chain import _fact, _item_match, ORG, YEAR


#: 与 _item_match 内部构造同文同序（金额位数一致），条目偏移可回原文映射。
NARRATIVE = "23、社会保障和就业支出（类）行政事业单位离退休（款）事业单位离退休（项）。支出决算为 60.00 万元。"

#: 交叉证据网格片段身份：锚事实的原格 ID 必须能回到该片段（C3）。
CROSS_GRID_UID = "crossgrid"


def _cross_grid_artifact(cross):
    """C3（2026-11-14 复验）：锚证据必须绑定当前原件——补最小真实解析产物。

    每个交叉表事实对应一行「编码｜名称｜金额」原生格；evidence_refs 指向
    金额格、row_identity_sources 指向编码格，均可回原格逐字核验。
    """
    cells, rows = [], []
    for index, fact in enumerate(cross):
        code = str(fact.subject_code)
        name = str(fact.subject_name)
        amount = str(fact.raw_value)
        row_top = 10.0 * index
        for col, text in ((0, code), (1, name), (2, amount)):
            cells.append(ParsedCell(
                row_idx=index, col_idx=col, raw_text=text, normalized_text=text,
                numeric_value=None, page_number=2,
                bbox=(10.0 * col, row_top, 10.0 * col + 9.0, row_top + 9.0),
                is_header=False, confidence=1.0, unit_hint="万元",
                extraction_method="lines"))
        rows.append([code, name, amount])
        fact.extra_dims["evidence_refs"] = [EvidenceRef(
            file_hash="a" * 64, physical_page=2,
            source_cell_id=f"{CROSS_GRID_UID}:r{index}c2", quote=amount,
            bbox=(20.0, row_top, 29.0, row_top + 9.0),
            location_level="exact_cell").to_dict()]
        fact.extra_dims["row_identity_sources"] = [{
            "file_hash": "a" * 64, "physical_page": 2,
            "source_cell_id": f"{CROSS_GRID_UID}:r{index}c0",
            "grid_raw_text": code, "recovered_text": code,
            "native_text_sources": [{"physical_page": 2,
                                     "bbox": [0.0, row_top, 9.0, row_top + 9.0]}],
        }]
    table = ParsedTable(
        table_code="FIN_03_expenditure", title="支出决算表", page_number=2,
        table_index=0, bbox=(0.0, 0.0, 100.0, 100.0), header_row_count=0,
        confidence=1.0, extraction_method="lines", unit_hint="万元",
        rows=rows, cells=cells, fragment_uid=CROSS_GRID_UID)
    return ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run",
                         physical_fragments=[table])


def welfare_document(*, cross_rows=(), extra_target_rows=()):
    """复刻福利院 208 链：FIN_05 目标表 + 同文其他原表（FIN_02/FIN_03）。"""
    target = [
        _fact("208", "社会保障和就业支出"),
        _fact("20805", "行政事业单位离退休"),
        _fact("2080502", "事业单离退休", raw="48.74"),
        *extra_target_rows,
    ]
    cross = []
    for table_code, code, name in cross_rows:
        fact = _fact(code, name)
        fact.fact_uid = f"cross-{table_code}-{code}"
        fact.table_code = table_code
        cross.append(fact)
    facts = target + cross
    context = FactContext(facts, "a" * 64, "run", org_name=ORG, fiscal_year=YEAR)

    class _Doc:
        page_texts = [NARRATIVE]
        fact_context = context
        parse_artifact = _cross_grid_artifact(cross) if cross else None

    return _Doc(), target


def test_real_welfare_shape_binds_via_cross_table_anchor():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
        ("FIN_02_income", "2080502", "事业单位离退休"),
    ])
    aliases = []
    fact, error = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"),
        aliases=aliases)
    assert error is None and fact is not None
    assert fact.subject_code == "2080502"
    assert fact.normalized_value == Decimal("48.74")
    proof = next(a for a in aliases if isinstance(a, dict) and a.get("role") == "cross_table_code_anchor")
    assert proof["basis"] == "same_document_printed_code_with_exact_name"
    assert set(proof["anchor_tables"]) == {"FIN_03_expenditure", "FIN_02_income"}
    assert proof["narrative_name"] == "事业单位离退休"
    assert proof["target_table_name"] == "事业单离退休"


def test_no_cross_table_exact_name_stays_blocked():
    # 其他原表同编码也写错名称：无交叉证据，不得绑定（保持原有 fail-closed）。
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退"),
    ])
    fact, error = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None and error


def test_same_name_two_codes_in_cross_tables_fails_closed():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
        ("FIN_02_income", "2080599", "事业单位离退休"),
    ])
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "同名对应两个不同编码时身份不唯一，不得绑定"


def test_anchor_outside_bound_parent_chain_fails_closed():
    # 交叉表同名行编码在另一款下：前缀不符，不绑定。
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080299", "事业单位离退休"),
    ])
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None


def test_identical_target_name_never_reaches_anchor():
    # 目标表名称本就一致：常规逐字路径应先命中，锚不参与。
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    target[2] = _fact("2080502", "事业单位离退休", raw="48.74")
    aliases = []
    fact, error = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"),
        aliases=aliases)
    assert error is None and fact.subject_code == "2080502"
    assert not any(isinstance(a, dict) and a.get("role") == "cross_table_code_anchor" for a in aliases)


def test_conflicting_target_names_for_anchor_code_fails_closed():
    # 目标表内同码出现两个不同名称：身份有冲突，不绑定。
    from copy import deepcopy

    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    duplicate = deepcopy(target[2])
    duplicate.fact_uid += ":dup"
    duplicate.subject_name = "事业离退休"
    target.append(duplicate)
    doc.fact_context.facts = target
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None


# ---------------------------------------------------------------------------
# C3（2026-11-14 复验）：锚证据必须绑定当前原件——换文件哈希、指向不存在
# 的源格、跨期、非决算阶段、低识别资格的锚一律不得升级可信身份。
# ---------------------------------------------------------------------------

def _last_cross_fact(doc):
    return doc.fact_context.facts[-1]


def test_anchor_from_other_file_hash_not_bound():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    for ref in _last_cross_fact(doc).extra_dims["evidence_refs"]:
        ref["file_hash"] = "b" * 64
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "证据指向另一文件时不得充当同文身份证明"


def test_anchor_with_nonexistent_source_cell_not_bound():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    for ref in _last_cross_fact(doc).extra_dims["evidence_refs"]:
        ref["source_cell_id"] = "nonexistent-cell"
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "源格不存在时不得绑定"


def test_anchor_with_wrong_code_cell_ref_not_bound():
    # 编码原格引用指向不存在的格：同样拒绝（row_identity_sources 也在核验范围）。
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    _last_cross_fact(doc).extra_dims["row_identity_sources"][0]["source_cell_id"] = "no-such-cell"
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None


def test_anchor_in_prior_period_not_bound():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    _last_cross_fact(doc).period = "年初"
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "跨期（年初等）行不充当本年身份证明"


def test_anchor_with_unknown_stage_not_bound():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    _last_cross_fact(doc).budget_stage = None
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "非决算阶段（含未识别）行不充当身份证明"


def test_anchor_with_low_qualification_status_not_bound():
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    _last_cross_fact(doc).recognition_status = "observed_blank"
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None, "空白观测等低资格行不充当身份证明"


def test_anchor_with_mismatched_parse_run_not_bound():
    # 解析批次与当前原件不一致：证据可能来自旧解析，不得绑定。
    doc, target = welfare_document(cross_rows=[
        ("FIN_03_expenditure", "2080502", "事业单位离退休"),
    ])
    doc.parse_artifact.parse_run_uid = "run-stale"
    fact, _ = _hierarchy_fact(
        doc, target, _item_match("社会保障和就业支出", "行政事业单位离退休", "事业单位离退休"))
    assert fact is None
