"""R10 两项共性机制修复的固定预期回归（2026-11-12）。

R10-01：后续未完成声明只阻断依赖它的关系——组内已确认的数量矛盾
（finding + fail 回执）必须保留，未完成另键记 insufficient_data，
规则聚合 insufficient_data 并交付 partial_issues。
R10-02：预期指标集合由原件表头独立建立，双向核验——候选表头未知/
重名导致映射缺项必须 GAP，不能只验成功映射子集后 PASS。

预期来自 Codex 第十轮核验包冻结断言。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rules_v33 import R33250_PerformanceCounts  # noqa: E402
from src.services.pdf_parser import PDFParser  # noqa: E402
import d1_table_reconciliation as d1  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BUSINESS_PDF = FIXTURES / "r8" / "expenditure_header_business.pdf"

BASE = ('绩效自评的2025年度项目53个，全部项目评级结果如下：'
        '绩效评级为“优”的项目50个，其余评级均为0个。')


def _run(body):
    doc = SimpleNamespace(
        path='甲局2025年度部门决算.pdf', page_tables=[[]],
        page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n' + body],
        report_kind='final', fiscal_year=2025, organization_name='甲局')
    issues, outcomes = run_rules_with_outcomes(
        doc, report_kind='final', rules=[R33250_PerformanceCounts()])
    return issues, [value.to_dict() for value in outcomes], list(getattr(doc, 'fact_receipts', []))


# ---------------------------------------------------------------------------
# R10-01：已确认问题不因后续缺数丢失
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('suffix,missing', [
    ('', False),
    ('评级结果如下。', True),
    ('相关材料如下：归档目录。评级结果如下。', True),
])
def test_known_failure_preserved_with_later_incomplete(suffix, missing):
    issues, outcomes, receipts = _run(BASE + suffix)
    known = [issue for issue in issues
             if (issue.location or {}).get('declared_count') == 53
             and (issue.location or {}).get('computed_count') == 50]
    assert len(known) == 1
    statuses = {receipt.get('status') for receipt in receipts}
    assert 'fail' in statuses
    assert [value['status'] for value in outcomes] == [
        'insufficient_data' if missing else 'fail']
    if missing:
        assert 'insufficient_data' in statuses
        keys = [receipt.get('sub_check_id') for receipt in receipts]
        assert len(set(keys)) == len(keys), '未完成与 fail 回执不得同键覆盖'
    else:
        assert 'insufficient_data' not in statuses


def test_missing_only_cases_stay_zero_material_issues():
    """无独立已完成关系时，缺数三例仍 0 材料问题且 insufficient_data。"""
    for body in [
        '绩效自评的2025年度项目53个，评级结果如下。',
        '绩效自评的2025年度项目53个，评级结果如下。评级数量尚未披露。归档材料如下：项目自评表、整改资料。',
        '绩效自评的2025年度项目53个，评级结果如下。相关材料如下：自评表。评级结果如下。',
    ]:
        issues, outcomes, receipts = _run(body)
        assert issues == []
        assert [value['status'] for value in outcomes] == ['insufficient_data']
        assert any(receipt.get('status') == 'insufficient_data' for receipt in receipts)


# ---------------------------------------------------------------------------
# R10-02：原件预期集合双向覆盖
# ---------------------------------------------------------------------------

def _rename_header(artifact, new_name):
    bad = copy.deepcopy(artifact)
    changes = set()
    for fragment in list(bad.table_fragments) + list(bad.physical_fragments):
        for ri, row in enumerate(fragment.rows):
            if len(row) == 4 and row[2] == '基本支出':
                row[2] = new_name
                changes.add((fragment.fragment_uid, ri, 2))
                for cell in fragment.cells:
                    if cell.row_idx == ri and cell.col_idx == 2:
                        cell.raw_text = new_name
    assert len(changes) == 1
    return bad


@pytest.mark.parametrize('new_name,missing_measures', [
    ('其他支出', ['basic_actual']),
    ('项目支出', ['basic_actual', 'project_actual']),
])
def test_unknown_or_duplicate_header_missing_measure_is_gap(new_name, missing_measures):
    digest = hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest()
    normal = PDFParser(None).build_parse_artifact(str(BUSINESS_PDF))
    before_data = json.dumps([f.rows[1:] for f in normal.table_fragments],
                             ensure_ascii=False, sort_keys=True)
    bad = _rename_header(normal, new_name)
    assert json.dumps([f.rows[1:] for f in bad.table_fragments],
                      ensure_ascii=False, sort_keys=True) == before_data
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=bad):
        report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert (report['pass'], report['gap']) == (0, 1)
    evidence = report['results'][0]['semantic_evidence']
    assert evidence['verification'] == 'failed'
    assert evidence['reason'] == 'header_semantic_measure_missing'
    mismatch = evidence['header_mismatches'][0]
    assert mismatch['kind'] == 'header_semantic_measure_missing'
    assert mismatch['missing_measures'] == missing_measures
    assert mismatch['expected_measures'] == ['basic_actual', 'project_actual', 'total_actual']
    assert hashlib.sha256(BUSINESS_PDF.read_bytes()).hexdigest() == digest


def test_normal_coverage_adopts_full_expected_set():
    report = d1.reconcile_auto(str(BUSINESS_PDF))
    assert (report['pass'], report['gap']) == (1, 0)
    evidence = report['results'][0]['semantic_evidence']
    assert evidence['adopted'] is True
    assert evidence['verification'] == 'complete'
    assert evidence['expected_measures'] == ['basic_actual', 'project_actual', 'total_actual']
    assert evidence['measures'] == {
        'total_actual': 1, 'basic_actual': 2, 'project_actual': 3}


def test_verification_status_explicit_on_fallback():
    import pdfplumber
    artifact = PDFParser(None).build_parse_artifact(str(BUSINESS_PDF))
    with pdfplumber.open(str(BUSINESS_PDF)) as pdf_doc:
        page_texts = [(page.extract_text() or '') for page in pdf_doc.pages]
    doc = d1.doc_for_artifact(artifact, str(BUSINESS_PDF), page_texts)
    instance = next(item for item in artifact.logical_tables
                    if item.table_code == 'FIN_03_expenditure')
    pages = sorted(d1._instance_pages(doc, instance))
    merged_rows, _errors = d1._logical_view_with_provenance(doc, [instance])
    _roles, evidence = d1._semantic_amount_roles(
        doc, instance, merged_rows, page_texts, pages, page_tables=[])
    assert evidence['adopted'] is False
    assert evidence['verification'] == 'incomplete'
    unknown = SimpleNamespace(table_code='UNKNOWN_misc', title='杂表')
    _roles, evidence = d1._semantic_amount_roles(
        doc, unknown, merged_rows, page_texts, pages, page_tables=[])
    assert evidence['verification'] == 'not_applicable'