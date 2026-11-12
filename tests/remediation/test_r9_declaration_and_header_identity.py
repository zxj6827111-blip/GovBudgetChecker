"""R9 两项共性机制修复的固定预期回归（2026-11-12）。

R9-01：逐评级声明应答记账——无关材料标题不能豁免独立未完成的评级声明；
材料标题之后新出现的未应答声明同样记未完成。窄义评级材料清单应答
（R6 冻结例）保留不建关系、不产回执。
R9-02：语义指标列身份对回原件表头格/列位（V4）——叶栏目互换、父栏目
互换都必须 GAP；表头不可判定时退回现有逻辑，不误杀正常产物。

预期来自 Codex 第九轮核验包冻结断言；夹具含 Codex 冻结原件（r8/）与
本地生成的父子表头控制原件（r9/，生成脚本见 make_parent_header_fixture.py）。
"""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from src.engine.native_final_disclosure import (  # noqa: E402
    _rating_material_title,
    check_performance_counts,
)
from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import R33250_PerformanceCounts, Rule  # noqa: E402
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402
import d1_table_reconciliation as d1  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BUSINESS_PDF = FIXTURES / "r8" / "expenditure_header_business.pdf"
PARENT_PDF = FIXTURES / "r9" / "parent_header_income_expenditure.pdf"

BASE = '绩效自评的2025年度项目53个，'


class PerformanceRule(Rule):
    code = 'V33-250'
    severity = 'error'


def _run(body):
    doc = SimpleNamespace(
        path='甲局2025年度部门决算.pdf', page_tables=[[]],
        page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
        report_kind='final', fiscal_year=2025, organization_name='甲局')
    issues, outcomes = run_rules_with_outcomes(
        doc, report_kind='final', rules=[R33250_PerformanceCounts()])
    return issues, [value.to_dict() for value in outcomes], list(getattr(doc, 'fact_receipts', []))


# ---------------------------------------------------------------------------
# R9-01：独立未完成声明不得被无关材料标题豁免
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('body', [
    BASE + '评级结果如下。',
    BASE + '评级结果如下。评级数量尚未披露。归档材料如下：项目自评表、整改资料。',
    BASE + '评级结果如下。相关材料如下：自评表。评级结果如下。',
])
def test_unfulfilled_declaration_stays_insufficient_data(body):
    issues, outcomes, receipts = _run(body)
    assert issues == []
    assert [value['status'] for value in outcomes] == ['insufficient_data']
    assert any(receipt.get('status') == 'insufficient_data' for receipt in receipts)


def test_unfulfilled_later_declaration_after_answered_counts():
    """已有数量应答第一个声明，其后新声明无数量 → 未完成另记，
    但组内已确认的 53≠50 确定问题与 fail 回执必须保留（R10-01 纠正：
    原新增自测误把 issues==[] 写成预期，与已裁业务规则冲突）。"""
    issues, outcomes, receipts = _run(
        BASE + '评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。评级结果如下。')
    assert len(issues) == 1
    assert issues[0].location['declared_count'] == 53
    assert issues[0].location['computed_count'] == 50
    assert [value['status'] for value in outcomes] == ['insufficient_data']
    statuses = {receipt.get('status') for receipt in receipts}
    assert statuses == {'fail', 'insufficient_data'}


def test_narrow_rating_material_closure_keeps_no_receipt():
    """R6 冻结例：评级材料清单应答声明 → 不建关系、无回执、不吸示例。"""
    doc = SimpleNamespace(
        page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'
                    + BASE + '评级结果如下。相关评级材料如下：项目自评表、整改资料。'
                    '绩效评级为“良”的项目3个。'],
        report_kind='final', fiscal_year=2025, organization_name='甲局')
    assert check_performance_counts(PerformanceRule(), doc) == []
    assert getattr(doc, 'fact_receipts', []) == []


def test_complete_relation_with_archive_clause_still_fails():
    issues, outcomes, receipts = _run(
        BASE + '相关材料已归档，评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。')
    assert len(issues) == 1
    assert [value['status'] for value in outcomes] == ['fail']
    assert receipts[-1]['status'] == 'fail'


@pytest.mark.parametrize('text,expected', [
    ('相关评级材料如下', True),
    ('评级资料如下', True),
    ('绩效评价材料如下', True),
    ('归档材料如下', False),
    ('相关材料如下', False),
    ('材料清单如下', False),
])
def test_rating_material_title_narrow_modifier(text, expected):
    compact = ''.join(text.split())
    title = compact.index('材料') if '材料' in compact else compact.index('资料')
    if '清单' in compact:
        title = compact.index('清单')
    assert _rating_material_title(compact, 0, title) is expected


# ---------------------------------------------------------------------------
# R9-02：表头语义身份对回原件（叶栏目互换 / 父栏目互换 / 退回）
# ---------------------------------------------------------------------------

def _swap_headers(artifact, old_a, old_b, cols):
    bad = copy.deepcopy(artifact)
    changed = False
    for fragment in list(bad.table_fragments) + list(bad.physical_fragments):
        for ri, row in enumerate(fragment.rows):
            if (len(row) > max(cols) and row[cols[0]] == old_a
                    and row[cols[1]] == old_b):
                row[cols[0]], row[cols[1]] = old_b, old_a
                for cell in fragment.cells:
                    if cell.row_idx == ri and cell.col_idx in cols:
                        cell.raw_text = row[cell.col_idx]
                changed = True
    assert changed
    return bad


def test_leaf_header_swap_is_gap_with_identity_evidence():
    digest = hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(BUSINESS_PDF))
    bad = _swap_headers(artifact, '基本支出', '项目支出', (2, 3))
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=bad):
        report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert (report['pass'], report['gap']) == (0, 1)
    result = report['results'][0]
    evidence = result['semantic_evidence']
    assert evidence['reason'] == 'header_semantic_identity_mismatch'
    mismatch = evidence['header_mismatches'][0]
    assert mismatch['kind'] == 'header_semantic_identity_mismatch'
    assert mismatch['order_errors']
    assert hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest() == digest


def test_parent_header_swap_is_gap_with_parent_evidence():
    digest = hashlib.sha256(PARENT_PDF.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(PARENT_PDF))
    bad = copy.deepcopy(artifact)
    changed = False
    for fragment in list(bad.table_fragments) + list(bad.physical_fragments):
        for ri, row in enumerate(fragment.rows):
            if len(row) == 4 and row[0] == '收入' and row[2] == '支出':
                row[0], row[1], row[2], row[3] = '支出', '支出', '收入', '收入'
                for cell in fragment.cells:
                    if cell.row_idx == ri:
                        cell.raw_text = row[cell.col_idx]
                changed = True
    assert changed
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=bad):
        report = d1.reconcile_auto(str(PARENT_PDF))
    assert (report['pass'], report['gap']) == (0, 1)
    evidence = report['results'][0]['semantic_evidence']
    assert evidence['reason'] == 'header_semantic_identity_mismatch'
    mismatch = evidence['header_mismatches'][0]
    assert mismatch['parent_errors']
    assert any(item['nearest_original_header'] == '收入'
               for item in mismatch['parent_errors'])
    assert hashlib.sha256(PARENT_PDF.read_bytes()).hexdigest() == digest


def test_parent_header_normal_adopts_semantic_roles():
    report = d1.reconcile_auto(str(PARENT_PDF))
    assert (report['pass'], report['gap']) == (1, 0)
    evidence = report['results'][0]['semantic_evidence']
    assert evidence['adopted'] is True
    assert evidence['measures'] == {'income_actual': 1, 'expenditure_actual': 3}


def test_v4_inconclusive_falls_back_without_gap():
    """原件表头不可判定（无独立表格通道）→ 退回，不改判 GAP。"""
    import pdfplumber
    artifact = PDFParser(None).build_parse_artifact(str(BUSINESS_PDF))
    with pdfplumber.open(str(BUSINESS_PDF)) as pdf_doc:
        page_texts = [(page.extract_text() or '') for page in pdf_doc.pages]
    doc = d1.doc_for_artifact(artifact, str(BUSINESS_PDF), page_texts)
    instance = next(item for item in artifact.logical_tables
                    if item.table_code == 'FIN_03_expenditure')
    pages = sorted(d1._instance_pages(doc, instance))
    merged_rows, _errors = d1._logical_view_with_provenance(doc, [instance])
    roles, evidence = d1._semantic_amount_roles(
        doc, instance, merged_rows, page_texts, pages, page_tables=[])
    assert roles is None
    assert evidence['adopted'] is False
    assert evidence['reason'] == 'header_identity_inconclusive'
    assert not evidence.get('header_mismatches')


def test_original_header_rows_selects_table_and_keeps_wrapped_cell():
    from src.services.fiscal_table_rules import NINE_TABLE_RULES
    rule = NINE_TABLE_RULES['FIN_03_expenditure']
    other = [[['杂项', '备注'], ['甲', '乙']]]
    target = [[['项目', '本年支出合计', '基本支出', '项目支出', '对附属单位补'],
               ['', '', '', '', '助支出'],
               ['一般公共服务支出', '100', '20.50', '79.50', '0']]]
    rows = d1._original_header_rows([other + target], [1], rule)
    flat = [text for row in rows for _col, text in row]
    assert '本年支出合计' in flat
    assert '基本支出' in flat