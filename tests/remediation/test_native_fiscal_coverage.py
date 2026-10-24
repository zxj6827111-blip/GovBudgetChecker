"""固定原页78格续表/68格余额回归，另用不安全反例约束公开解析入口。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.schemas.parse_artifact import CellPresence
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import build_table_import_receipt

FIXTURES = Path(__file__).parents[1] / 'fixtures/native_fiscal_coverage'
CASES = json.loads((FIXTURES / 'manifest.json').read_text())


def context(artifact):
    result = build_fact_context_from_artifact(artifact, org_name='固定材料', fiscal_year=2024, report_kind='final')
    assert result
    return result


@pytest.fixture(scope='module')
def parsed_cases():
    results = {}
    for case in CASES:
        path = FIXTURES / case['filename']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case['fixture_sha256']
        artifact = PDFParser(None).build_parse_artifact(str(path))
        results[case['filename']] = artifact, context(artifact)
    return results


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['filename'])
def test_all_fixed_source_amounts_have_exact_column_subject_and_proof(parsed_cases, case):
    artifact, ctx = parsed_cases[case['filename']]
    physical = {f'{t.fragment_uid}:r{c.row_idx}c{c.col_idx}': c
                for t in artifact.physical_fragments for c in t.cells}
    for expected in case['expected']:
        identity = expected['expected']
        matches = [f for f in ctx.facts if f.subject_code == identity['subject_code']
                   and ''.join((f.subject_name or '').split()) == ''.join(identity['subject_name'].split())
                   and f.indicator == identity['indicator']
                   and any(r['physical_page'] == expected['page'] for r in f.extra_dims['evidence_refs'])]
        assert len(matches) == 1, expected['truth_id']
        fact = matches[0]
        assert fact.raw_value == expected['raw_text']
        assert operand_rejection_reason(fact, '固定材料') is None
        ref = fact.extra_dims['evidence_refs'][0]
        original = physical[ref['source_cell_id']]
        assert original.raw_text == fact.raw_value and list(original.bbox) == ref['bbox']
        sources = ref['source_provenance']['native_text_sources']
        assert sources and ''.join(s['text'] for s in sources) == fact.raw_value
        # 直接回读原生字符，不能只检查被测系统自称有证明。
        import pdfplumber
        with pdfplumber.open(FIXTURES / case['filename']) as pdf:
            for proof in sources:
                chars = pdf.pages[proof['physical_page'] - 1].chars
                assert ''.join(c['text'] for c in chars[proof['start_char_index']:proof['end_char_index']]) == proof['text']
    receipt = build_table_import_receipt(artifact, ctx.facts, org_name='固定材料')
    assert receipt['original_pdf_full_coverage_verified'] is False
    assert receipt['unresolved_money_cell_count'] == 0


@pytest.mark.parametrize('fault', ['shift_one_money_box', 'missing_character_proof', 'character_outside_lane', 'note_not_spanning'])
def test_public_pipeline_rejects_unsafe_collapsed_continuation(monkeypatch, fault):
    original = PDFParser._extract_tables_from_page

    def damaged(self, page, page_number, continuation=None):
        tables = original(self, page, page_number, continuation)
        if page_number == 2:
            for table in tables:
                if table.extraction_method not in {'lines', 'lines_strict'}:
                    continue
                cell = next(c for c in table.cells if c.numeric_value is not None and c.col_idx == 2)
                if fault == 'shift_one_money_box':
                    cell.bbox = (cell.bbox[0] + 10, *cell.bbox[1:])
                elif fault == 'missing_character_proof':
                    cell.native_text_sources = []
                elif fault == 'character_outside_lane':
                    cell.native_text_sources[0]['bbox'][0] = cell.bbox[0] - 10
                else:
                    note = next(c for c in table.cells if c.raw_text.startswith('注'))
                    note.bbox = (*note.bbox[:2], note.bbox[2] - 20, note.bbox[3])
        return tables

    monkeypatch.setattr(PDFParser, '_extract_tables_from_page', damaged)
    artifact = PDFParser(None).build_parse_artifact(str(FIXTURES / 'SH2024_01_continuation.pdf'))
    ctx = context(artifact)
    assert not [f for f in ctx.definite_facts() if any(r['physical_page'] == 2 for r in f.extra_dims['evidence_refs'])]
    receipt = build_table_import_receipt(artifact, ctx.facts, org_name='固定材料')
    assert receipt['pending_review_count'] > 0 and receipt['column_identity_unresolved_count'] > 0
    assert receipt['amount_cell_count'] >= 24


def test_receipt_counts_unmapped_balances_missing_facts_wrong_column_and_gate(parsed_cases):
    artifact, ctx = deepcopy(parsed_cases['SH2024_11_balance.pdf'])
    table = artifact.table_fragments[0]
    # 去掉字面列头模拟字段尚未覆盖：金额仍在原格，不能从分母消失。
    for cell in table.cells:
        if cell.col_idx in {4, 9} and cell.is_header:
            cell.raw_text = cell.normalized_text = ''
            table.rows[cell.row_idx][cell.col_idx] = ''
    remaining = [f for f in ctx.facts if f.indicator not in {'beginning_balance', 'ending_balance'}]
    r = build_table_import_receipt(artifact, remaining, org_name='固定材料')
    assert r['unmapped_amount_cell_count'] == r['fact_missing_count'] == 16
    assert r['amount_cell_count'] == len(ctx.facts)
    assert r['unresolved_money_cell_count'] == 16
    wrong = deepcopy(ctx.facts)
    wrong[0].indicator = 'wrong_column'
    wrong[1].recognition_status = 'low_confidence'
    r = build_table_import_receipt(parsed_cases['SH2024_11_balance.pdf'][0], wrong, org_name='固定材料')
    assert r['fact_conflict_count'] == 1 and r['rule_blocked_count'] >= 1
    assert r['column_identity_unresolved_count'] >= 1 and r['pending_review_count'] >= 2


@pytest.mark.parametrize('doc_type', ['final', 'dept_final', 'unit_final', 'district_final'])
def test_public_and_storage_fact_entrypoints_agree_on_final_stage(parsed_cases, doc_type):
    from src.services.structured_ingest_runner import _materialize_fiscal_facts_from_artifact
    artifact = parsed_cases['SH2024_11_balance.pdf'][0]
    public = build_fact_context_from_artifact(artifact, org_name='固定材料', fiscal_year=2024, doc_type=doc_type)
    stored = _materialize_fiscal_facts_from_artifact(artifact, org_name='固定材料', fiscal_year=2024,
                                                  doc_type=doc_type, file_hash=artifact.source_file_hash)
    assert public and stored
    assert {f.budget_stage for f in public.facts} == {'决算'}
    assert {f.budget_stage for f in stored} == {'决算'}
    assert [(f.fact_uid, f.raw_value, f.budget_stage) for f in stored] == [
        (f.fact_uid, f.raw_value, f.budget_stage) for f in public.facts]


@pytest.mark.parametrize('capital', [False, True])
@pytest.mark.parametrize('literal,presence', [('0.00', CellPresence.ZERO), ('', CellPresence.BLANK),
                                             ('12.34', CellPresence.VALUE),
                                             ('—', CellPresence.DASH), ('无此项', CellPresence.NOT_APPLICABLE), ('?', CellPresence.VALUE)])
def test_public_balance_columns_keep_zero_blank_dash_and_unknown_distinct(tmp_path, capital, literal, presence):
    from test_native_structure_pdf_pipeline import text, lines
    path = tmp_path / 'balance.pdf'
    title = '国有资本经营预算财政拨款收入支出决算表' if capital else '政府性基金预算财政拨款收入支出决算表'
    xs, ys = [40, 110, 290, 380, 470, 560, 650, 740, 840], [100, 140, 180, 220]
    with pymupdf.open() as doc:
        page = doc.new_page(width=900, height=400)
        text(page, 40, 60, title)
        text(page, 40, 80, '单位：万元')
        lines(page, xs, ys)
        for y, values in [(125, ['科目编码', '科目名称', '年初结转和结余', '本年收入', '合计', '基本支出', '项目支出', '年末结转和结余']),
                          (165, ['229', '其他支出', literal, '10.00', '10.00', '0.00', '10.00', literal]),
                          (205, ['合计', '', literal, '10.00', '10.00', '0.00', '10.00', literal])]:
            for col, value in enumerate(values):
                text(page, xs[col] + 2, y, value, 7)
        doc.save(path)
    artifact = PDFParser(None).build_parse_artifact(str(path))
    ctx = context(artifact)
    table = next(t for t in artifact.table_fragments if t.table_code.startswith('FIN_09' if capital else 'FIN_08'))
    cells = [c for c in table.cells if c.row_idx > 0 and c.col_idx in {2, 7}]
    assert len(cells) == 4 and {c.presence for c in cells} == {presence}
    balances = [f for f in ctx.facts if f.indicator in {'beginning_balance', 'ending_balance'}]
    assert len(balances) == (4 if literal in {'0.00', '12.34'} else 0)
    assert all(f.raw_value == literal for f in balances)
