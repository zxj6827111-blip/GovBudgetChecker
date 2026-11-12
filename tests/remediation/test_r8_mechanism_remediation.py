"""R8 两项共性机制修复的固定预期回归（2026-11-12）。

R8-01：材料组边界必须绑定同一披露对象与谓词——归档句（逗号/括号/分号
连接）不能借后句评级声明的"如下"截掉完整评级数量；有声明无操作数且无
材料标题应答时显式未完成，不允许零问题零回执静默 pass。
R8-02：已知九表的金额栏目身份复用生产语义列映射（V1-V3 独立核对门），
"本年支出合计/基本支出/项目支出"不因未写"金额"二字被整数统计学成编码；
任一金额格变异仍须 GAP，原件不变。

预期来自 Codex 第八轮核验包冻结断言与 R2-R7 已裁定边界，不按单位、
文件名、ID、固定页码或金额写特判。
"""
from __future__ import annotations

import copy
import hashlib
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from src.engine.native_final_disclosure import (  # noqa: E402
    _material_group_boundary,
    check_performance_counts,
)
from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import R33250_PerformanceCounts, Rule  # noqa: E402
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402
import d1_table_reconciliation as d1  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "r8"
BUSINESS_PDF = FIXTURES / "expenditure_header_business.pdf"
EXPLICIT_PDF = FIXTURES / "expenditure_header_explicit_amount.pdf"

PREFIX = '绩效自评的2025年度项目53个，'
COUNTS = '绩效评级为“优”的项目50个，其余评级均为0个。'
EQUALITY = 'self_evaluation_count = sum(disclosed distinct rating counts)'
INEQUALITY = 'self_evaluation_count >= sum(disclosed distinct rating counts)'


class PerformanceRule(Rule):
    code = 'V33-250'
    severity = 'error'


def _doc(body):
    return SimpleNamespace(
        page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
        report_kind='final', fiscal_year=2025, organization_name='甲局')


def _run(body):
    doc = _doc(body)
    try:
        issues = check_performance_counts(PerformanceRule(), doc)
        signal = None
    except RuleDeferred as exc:
        issues = list(exc.partial_issues)
        signal = str(exc)
    runner_doc = SimpleNamespace(
        path='甲局2025年度部门决算.pdf', page_tables=[[]],
        page_texts=doc.page_texts, report_kind='final',
        fiscal_year=2025, organization_name='甲局')
    runner_issues, outcomes = run_rules_with_outcomes(
        runner_doc, report_kind='final', rules=[R33250_PerformanceCounts()])
    return issues, signal, list(getattr(doc, 'fact_receipts', [])), runner_issues, outcomes


# ---------------------------------------------------------------------------
# R8-01：归档谓词不得借用评级谓词（冻结 A-E + 部分列举对照）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('archive', [
    '',
    '相关材料已归档。',
    '相关材料已归档，',
    '（相关材料已归档）',
    '相关材料已归档；',
])
def test_archive_clause_does_not_delete_complete_rating_relation(archive):
    issues, signal, receipts, runner_issues, outcomes = _run(
        PREFIX + archive + '评级结果如下。' + COUNTS)
    assert signal is None
    assert len(issues) == 1
    assert len(receipts) == 1
    assert receipts[0]['status'] == 'fail'
    assert receipts[0]['formula'] == EQUALITY
    location = issues[0].location
    assert location['rating_counts'] == {'优': 50}
    assert location['computed_count'] == 50
    assert location['declared_count'] == 53
    assert len(runner_issues) == 1
    assert [value.to_dict()['status'] for value in outcomes] == ['fail']


def test_partial_enumeration_stays_inequality():
    issues, signal, receipts, runner_issues, outcomes = _run(
        PREFIX + '其中，绩效评级为“优”的项目50个。')
    assert signal is None
    assert issues == []
    assert len(receipts) == 1
    assert receipts[0]['status'] == 'pass'
    assert receipts[0]['formula'] == INEQUALITY
    assert [value.to_dict()['status'] for value in outcomes] == ['pass']


def test_material_title_still_terminates_group_without_receipt():
    """R6/R7 冻结边界：材料标题先于数量时不建关系、无回执、不吸示例。"""
    issues, signal, receipts, _runner_issues, _outcomes = _run(
        PREFIX + '评级结果如下。相关评级材料如下：项目自评表、整改资料。'
        '绩效评级为“良”的项目3个。')
    assert signal is None
    assert issues == []
    assert receipts == []


@pytest.mark.parametrize('count,expected_issues,status,formula', [
    (3, 1, 'fail', EQUALITY),
    (10, 0, 'pass', INEQUALITY),
])
def test_other_material_example_does_not_join_operands(count, expected_issues, status, formula):
    body = (PREFIX + ('评级结果如下。' if count == 3 else '其中，')
            + '绩效评级为“优”的项目50个'
            + ('，其余评级均为0个。' if count == 3 else '。')
            + f'相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目{count}个。')
    issues, signal, receipts, _runner_issues, _outcomes = _run(body)
    assert signal is None
    assert len(issues) == expected_issues
    assert receipts[-1]['status'] == status
    assert receipts[-1]['formula'] == formula
    spans = [str(source.get('span') or '')
             for source in receipts[-1].get('disclosure_sources') or []]
    assert not any('良' in span or '示例' in span for span in spans)
    if issues:
        assert issues[0].location['rating_counts'] == {'优': 50}


def test_declaration_without_operands_or_material_title_is_explicitly_incomplete():
    """零回执守卫：有评级披露谓词但无数量、无材料标题 → 显式未完成。"""
    issues, signal, receipts, _runner_issues, outcomes = _run(
        PREFIX + '评级结果如下。')
    assert signal is not None
    assert issues == []
    assert len(receipts) == 1
    assert receipts[0]['status'] == 'insufficient_data'
    assert '评级声明存在但未提取到同组评级数量' in signal
    assert [value.to_dict()['status'] for value in outcomes] != ['pass']


def test_no_declaration_no_relation_stays_silent():
    issues, signal, receipts, _runner_issues, _outcomes = _run(
        PREFIX + '平均得分95.55分。')
    assert signal is None
    assert issues == []
    assert receipts == []


# ---------------------------------------------------------------------------
# R8-01：边界函数单元判据（对象-谓词绑定，不是分隔符补丁）
# ---------------------------------------------------------------------------

def _compact(value):
    return ''.join(unicodedata.normalize('NFKC', value).split())


@pytest.mark.parametrize('text,expected_anchor', [
    ('评级结果如下。', None),
    ('相关材料已归档,评级结果如下。', None),
    ('相关材料已归档)评级结果如下。', None),
    ('相关材料已归档;评级结果如下。', None),
    ('相关材料已归档。评级结果如下。', None),
    ('情况如下。', None),
    ('相关评级材料如下：示例。', '材料'),
    ('材料清单如下：', '清单'),
    ('评级资料如下：', '资料'),
])
def test_boundary_binds_nearest_object(text, expected_anchor):
    compact = _compact(text)
    boundary = _material_group_boundary(compact, 0, len(compact))
    if expected_anchor is None:
        assert boundary is None
    else:
        assert boundary is not None
        assert compact[boundary:boundary + len(expected_anchor)] == expected_anchor


def test_boundary_takes_earliest_of_multiple_material_titles():
    compact = _compact('评级结果如下。材料清单如下：甲。相关资料如下：乙。')
    boundary = _material_group_boundary(compact, 0, len(compact))
    assert boundary == compact.index('清单')


def test_boundary_object_must_share_clause_with_predicate():
    compact = _compact('材料已归档。评级结果如下。')
    assert _material_group_boundary(compact, 0, len(compact)) is None


# ---------------------------------------------------------------------------
# R8-02：已知财政表语义列身份（正常/变异/核对门）
# ---------------------------------------------------------------------------

EXPECTED_ROWS = [
    ['一般公共服务支出', '100', '20.50', '79.50'],
    ['教育支出', '200', '80.50', '119.50'],
    ['合计', '300', '101.00', '199.00'],
]


def _artifact(pdf):
    return PDFParser(None).build_parse_artifact(str(pdf))


def _fin03(artifact):
    return next(inst for inst in artifact.logical_tables
                if inst.table_code == 'FIN_03_expenditure')


def test_business_header_rows_equal_independent_expected():
    fragment = next(f for f in _artifact(BUSINESS_PDF).table_fragments
                    if f.table_code == 'FIN_03_expenditure')
    data_rows = [[str(cell) for cell in row] for row in fragment.rows
                 if any(str(cell).strip() and str(cell)[0].isdigit()
                        for cell in row[1:])]
    assert data_rows == EXPECTED_ROWS


def test_business_header_semantic_roles_adopted_and_pass():
    report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert (report['pass'], report['gap']) == (1, 0)
    result = report['results'][0]
    evidence = result['semantic_evidence']
    assert evidence['adopted'] is True
    assert evidence['amount_cols'] == [1, 2, 3]
    assert evidence['measures'] == {
        'total_actual': 1, 'basic_actual': 2, 'project_actual': 3}
    assert result['missing_numbers'] == {}
    assert result['row_mismatches'] == []


def test_explicit_amount_header_keeps_header_keyword_path():
    report = d1.reconcile_auto(str(EXPLICIT_PDF))
    assert (report['pass'], report['gap']) == (1, 0)
    evidence = report['results'][0]['semantic_evidence']
    assert evidence['adopted'] is False
    assert evidence['reason'] == 'empty_mapping'


@pytest.mark.parametrize('row_index', [1, 2, 3])
@pytest.mark.parametrize('col_index', [1, 2, 3])
def test_any_amount_cell_mutation_is_gap(row_index, col_index):
    digest = hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest()
    artifact = _artifact(BUSINESS_PDF)
    fragment = next(f for f in artifact.table_fragments
                    if f.table_code == 'FIN_03_expenditure')
    before = str(fragment.rows[row_index][col_index])
    places = len(before.split('.')[1]) if '.' in before else 0
    after = format(Decimal(before) + Decimal(1), f'.{places}f')
    fragment.rows[row_index][col_index] = after
    for cell in fragment.cells:
        if cell.row_idx == row_index and cell.col_idx == col_index:
            cell.raw_text = after
            cell.numeric_value = Decimal(after)
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=artifact):
        report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert (report['pass'], report['gap']) == (0, 1)
    assert hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest() == digest


def _semantic_inputs(pdf):
    import pdfplumber
    artifact = _artifact(pdf)
    with pdfplumber.open(str(pdf)) as pdf_doc:
        page_texts = [(page.extract_text() or '') for page in pdf_doc.pages]
    doc = d1.doc_for_artifact(artifact, str(pdf), page_texts)
    instance = _fin03(artifact)
    pages = sorted(d1._instance_pages(doc, instance))
    merged_rows, _errors = d1._logical_view_with_provenance(doc, [instance])
    return doc, instance, merged_rows, page_texts, pages


def test_v1_gate_rejects_title_absent_from_original():
    doc, instance, merged_rows, page_texts, pages = _semantic_inputs(BUSINESS_PDF)
    impostor = copy.copy(instance)
    impostor.title = '收入决算表'
    roles, evidence = d1._semantic_amount_roles(
        doc, impostor, merged_rows, page_texts, pages)
    assert roles is None
    assert evidence['adopted'] is False
    assert evidence['gate'] == 'V1'


def test_v3_gate_rejects_non_numeric_mapped_data_cell():
    doc, instance, merged_rows, page_texts, pages = _semantic_inputs(BUSINESS_PDF)
    corrupted = copy.deepcopy(merged_rows)
    corrupted[1]['cells'][1] = '文本'
    roles, evidence = d1._semantic_amount_roles(
        doc, instance, corrupted, page_texts, pages)
    assert roles is None
    assert evidence['gate'] == 'V3'
    assert evidence['reason'] == 'mapping_col_non_numeric_data'


def test_semantic_roles_do_not_override_original_side_identity():
    """原件侧仍按 token 顺序解析：金额列角色不改变原件身份与序列。"""
    _doc, _instance, _merged, page_texts, pages = _semantic_inputs(BUSINESS_PDF)
    records = d1._parse_original_page(page_texts[pages[0] - 1], pages[0], set())
    identities = [record['identity'] for record in records]
    assert identities == ['一般公共服务支出', '教育支出', '合计']
    assert [record['numbers'] for record in records] == [
        ['1E+2', '20.5', '79.5'], ['2E+2', '80.5', '119.5'], ['3E+2', '101', '199']]


def test_missing_merged_row_is_reverse_reported():
    artifact = _artifact(BUSINESS_PDF)
    fragment = next(f for f in artifact.table_fragments
                    if f.table_code == 'FIN_03_expenditure')
    fragment.rows = [row for index, row in enumerate(fragment.rows) if index != 2]
    fragment.cells = [cell for cell in fragment.cells if cell.row_idx != 2]
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=artifact):
        report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert report['gap'] == 1
    kinds = {item['kind'] for item in report['results'][0]['row_mismatches']}
    assert 'row_missing_in_merged' in kinds