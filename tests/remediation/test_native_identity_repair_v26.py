"""31格来源身份与空金额续页：公开PDF/财政事实入口固定回归。"""
import hashlib
import json
from pathlib import Path
import unicodedata

import pdfplumber
import pymupdf
import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.schemas.parse_artifact import ParseArtifact
from src.services.pdf_parser import PDFParser
from src.services.table_import_receipt import build_table_import_receipt

FIXTURES = Path(__file__).parents[1] / 'fixtures/native_identity_v26'
CASES = json.loads((FIXTURES / 'manifest.json').read_text())


def compact(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split())


@pytest.fixture(scope='module')
def parsed():
    results = {}
    for case in CASES:
        path = FIXTURES / case['filename']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case['fixture_sha256']
        artifact = PDFParser(None).build_parse_artifact(str(path))
        ctx = build_fact_context_from_artifact(artifact, fiscal_year=2024, report_kind='final')
        results[case['doc_id']] = artifact, ctx.facts if ctx else []
    return results


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['doc_id'])
def test_fixed_original_amounts_subjects_columns_and_character_identity(parsed, case):
    artifact, facts = parsed[case['doc_id']]
    assert len(facts) == len(case['expected'])
    with pdfplumber.open(FIXTURES / case['filename']) as pdf:
        for source in case['expected']:
            anchor = source['native_source']['start_char_index']
            matches = [fact for fact in facts for ref in fact.extra_dims['evidence_refs']
                       if ref['physical_page'] == source['page'] and any(
                           p['start_char_index'] <= anchor < p['end_char_index']
                           for p in ref['source_provenance']['native_text_sources'])]
            assert len(matches) == 1, source['truth_id']
            fact = matches[0]
            assert fact.raw_value == source['raw_text']
            for key in ('subject_code', 'subject_name', 'indicator'):
                if source['expected'].get(key) is not None:
                    assert compact(getattr(fact, key)) == compact(source['expected'][key]), source['truth_id']
            assert operand_rejection_reason(fact) is None
            independent = source['native_source']
            chars = pdf.pages[source['page'] - 1].chars[independent['start_char_index']:independent['end_char_index']]
            assert ''.join(c['text'] for c in chars) == source['raw_text']
            for proof in source['identity_sources']:
                if proof.get('method') == 'independent_native_word_center':
                    native = pdf.pages[source['page'] - 1].chars
                    selected = [c for c in native if any(
                        word[0] - .5 <= (c['x0'] + c['x1']) / 2 <= word[2] + .5
                        and word[1] - .5 <= (c['top'] + c['bottom']) / 2 <= word[3] + .5
                        for word in proof['source_words'])]
                    assert compact(''.join(c['text'] for c in selected)) == compact(proof['text'])
                    continue
                actual = pdf.pages[source['page'] - 1].chars[proof['start_char_index']:proof['end_char_index']]
                assert ''.join(c['text'] for c in actual) == proof['text']
                for recorded, char in zip(proof['characters'], actual, strict=True):
                    assert recorded['bbox'] == [char[k] for k in ('x0', 'top', 'x1', 'bottom')]
    assert ParseArtifact.from_dict(json.loads(json.dumps(artifact.to_dict()))).to_dict() == artifact.to_dict()
    receipt = build_table_import_receipt(artifact, facts)
    assert receipt['fact_missing_count'] == receipt['fact_conflict_count'] == receipt['rule_blocked_count'] == 0
    assert not receipt['original_pdf_full_coverage_verified']


def test_31_target_cells_are_covered_and_spaced_total_does_not_change_original_grid(parsed):
    assert sum(s['target'] for c in CASES for s in c['expected']) == 31
    originals = []
    for doc in ('SH2024N9_03', 'SH2024N9_06', 'SH2024N9_09'):
        artifact, facts = parsed[doc]
        originals.extend(c.raw_text for t in artifact.source_grids for c in t.cells if compact(c.raw_text) == '合计')
        assert all(f.subject_code == 'total' for f in facts if compact(f.subject_name) == '合计')
    assert any(' ' in text for text in originals)


def test_blank_previous_row_keeps_its_own_tail_and_total_source_is_clean(parsed):
    artifact, facts = parsed['SH2024N9_02']
    total, = [f for f in facts if f.subject_name == '总计' and f.indicator == 'income_actual']
    assert total.raw_value == '1,715.06'
    recovered = total.extra_dims['row_identity_sources']
    assert any(s['grid_raw_text'] == '款\n总计' and s['recovered_text'] == '总计' for s in recovered)
    blank_parent = [c for t in artifact.physical_fragments for c in t.cells
                    if c.raw_text == '三、国有资本经营预算财政拨款']
    assert blank_parent and blank_parent[0].native_text_sources
    assert any(c.raw_text == '款\n总计' for t in artifact.source_grids for c in t.cells)


def test_continuation_names_keep_their_own_full_character_runs(parsed):
    _, facts = parsed['SH2024N9_06']
    targets = [f for f in facts if f.subject_code in {'22499', '2249999'}]
    assert len(targets) == 14
    assert {f.subject_name for f in targets} == {'其他灾害防治及应急管理支出'}
    starts = {f.extra_dims['row_identity_sources'][0]['native_text_sources'][0]['start_char_index'] for f in targets}
    assert len(starts) == 2


def test_blank_economic_tail_is_a_table_region_but_creates_no_zero_facts(parsed):
    artifact, facts = parsed['SH2024N9_04']
    assert facts == []
    footer, = [t for t in artifact.physical_fragments if t.page_number == 2 and t.extraction_method == 'lines']
    logical, = [t for t in artifact.logical_tables if footer.fragment_uid in t.fragment_uids]
    assert logical.table_code == 'FIN_06_basic_expenditure' and not logical.identity_conflict
    assert any(c.raw_text == '31099' and c.col_idx == 3 for c in footer.cells)
    money = [c for c in footer.cells if c.col_idx in {2, 5}]
    assert len(money) == 4 and all(c.raw_text == '' and c.native_blank_verified and c.numeric_value is None for c in money)
    receipt = build_table_import_receipt(artifact, facts)
    assert receipt['amount_cell_count'] == receipt['unresolved_money_cell_count'] == 0
    exclusions = [c for t in receipt['tables'] for c in t['excluded_numeric_cells']]
    assert any(c['raw_text'] == '31099' and c['basis'] for c in exclusions)


@pytest.mark.parametrize('fault', ['missing_blank_proof', 'money_lane_shift', 'missing_code_proof', 'repeated_old_code', 'wrong_closing_label'])
def test_public_pipeline_keeps_unsafe_blank_continuation_unresolved(monkeypatch, fault):
    original = PDFParser._extract_tables_from_page

    def damaged(self, page, page_number, continuation=None):
        tables = original(self, page, page_number, continuation)
        if page_number == 2:
            for table in tables:
                if table.extraction_method != 'lines':
                    continue
                code = next(c for c in table.cells if c.raw_text == '31099')
                if fault == 'missing_code_proof':
                    code.native_text_sources = []
                elif fault == 'repeated_old_code':
                    code.raw_text = table.rows[code.row_idx][code.col_idx] = '31022'
                    code.native_text_sources[0]['text'] = '31022'
                elif fault == 'wrong_closing_label':
                    label = next(c for c in table.cells if c.raw_text == '公用经费合计')
                    label.raw_text = table.rows[label.row_idx][label.col_idx] = '另一项合计'
                else:
                    cell = next(c for c in table.cells if c.col_idx == 2)
                    if fault == 'missing_blank_proof':
                        cell.native_blank_verified = False
                    else:
                        cell.bbox = (cell.bbox[0] + 8, *cell.bbox[1:])
        return tables

    monkeypatch.setattr(PDFParser, '_extract_tables_from_page', damaged)
    artifact = PDFParser(None).build_parse_artifact(str(FIXTURES / 'SH2024N9_04.pdf'))
    tail = next(t for t in artifact.physical_fragments if t.page_number == 2 and t.extraction_method == 'lines')
    assert not [t for t in artifact.logical_tables if t.table_code == 'FIN_06_basic_expenditure' and tail.fragment_uid in t.fragment_uids]
    receipt = build_table_import_receipt(artifact, [])
    assert receipt['unresolved_money_cell_count'] > 0


def test_spaced_marker_normalization_does_not_rewrite_an_arbitrary_subject_name():
    from src.services.fiscal_fact_materializer import FiscalFactMaterializer
    result = FiscalFactMaterializer(None)._extract_classification([{'raw_text': '综合 计划工作'}])
    assert result == {'classification_code': None, 'classification_name': '综合 计划工作'}


@pytest.mark.parametrize('filename', ['independent_blank_total.pdf', 'independent_blank_total_offset.pdf'])
def test_independent_printed_total_is_not_swallowed_by_blank_preceding_row(filename):
    path = FIXTURES / filename
    meta = json.loads(path.with_suffix('.json').read_text())
    assert meta['synthetic'] and hashlib.sha256(path.read_bytes()).hexdigest() == meta['sha256']
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(artifact, report_kind='final')
    assert context and len(context.facts) == 2
    assert all(f.subject_code == 'total' and f.subject_name == '总计' and f.raw_value == '100.00'
               and operand_rejection_reason(f) is None for f in context.facts)
    physical = [t for t in artifact.physical_fragments if t.extraction_method == 'lines']
    assert [t.rows for t in physical] == [t.rows for t in artifact.source_grids]


def test_nonadjacent_grid_cannot_borrow_a_previous_header_for_identity_recovery(tmp_path):
    path = tmp_path / 'nonadjacent.pdf'
    with pymupdf.open(FIXTURES / 'SH2024N9_06.pdf') as source, pymupdf.open() as output:
        output.insert_pdf(source, from_page=0, to_page=0)
        output.new_page()
        output.insert_pdf(source, from_page=1, to_page=1)
        output.save(path)
    artifact = PDFParser(None).build_parse_artifact(str(path))
    tail, = [t for t in artifact.physical_fragments if t.page_number == 3 and t.extraction_method == 'lines']
    assert tail.table_code.startswith('UNKNOWN_')
    assert not [c for c in tail.cells if c.col_idx == 1 and c.native_text_sources]
    original, = [t for t in artifact.source_grids if t.page_number == 3]
    assert tail.rows == original.rows
    context = build_fact_context_from_artifact(artifact, report_kind='final')
    assert context and not [f for f in context.facts if any(r['physical_page'] == 3 for r in f.extra_dims['evidence_refs'])]


def test_independent_multiline_subject_after_blank_row_keeps_both_fiscal_facts():
    path = FIXTURES / 'independent_multiline_subject.pdf'
    meta = json.loads(path.with_suffix('.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == meta['fixture_sha256']
    artifact = PDFParser(None).build_parse_artifact(str(path))
    context = build_fact_context_from_artifact(artifact, report_kind='final')
    targets = [f for f in context.facts if f.subject_name == meta['expected_subject']]
    assert len(targets) == 2
    assert {f.indicator for f in targets} == {'expenditure_actual', 'general_public_budget'}
    assert all(f.raw_value == meta['expected_raw_value'] and operand_rejection_reason(f) is None
               for f in targets)
    # 真值来自修复前冻结原件；前一行空金额不应改变后行完整多行标签的归属。
    with pdfplumber.open(path) as pdf:
        for char in meta['native_characters']:
            actual = pdf.pages[1].chars[char['index']]
            assert actual['text'] == char['text']
            assert [actual[k] for k in ('x0', 'top', 'x1', 'bottom')] == char['bbox']
    for fact in targets:
        ref, = fact.extra_dims['evidence_refs']
        assert ref['physical_page'] == 2 and ref['source_cell_id'].endswith(('r6c3', 'r6c4'))
        original = next(c for t in artifact.source_grids for c in t.cells
                        if f'{t.fragment_uid}:r{c.row_idx}c{c.col_idx}' == ref['source_cell_id'])
        assert original.raw_text == fact.raw_value
