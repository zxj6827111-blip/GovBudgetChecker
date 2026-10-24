"""原页固定回归：39格名称、65格编码及2格尾部合计的身份与来源证明。"""
import hashlib
import json
from pathlib import Path

import pdfplumber
import pymupdf
import pytest

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason
from src.services.pdf_parser import PDFParser
from src.services.fiscal_table_rules import normalize_text
from src.services.table_import_receipt import build_table_import_receipt


FIXTURES = Path(__file__).parents[1] / 'fixtures/native_identity_v25'
CASES = json.loads((FIXTURES / 'manifest.json').read_text())


def _context(artifact):
    result = build_fact_context_from_artifact(artifact, org_name='固定原件', fiscal_year=2024, report_kind='final')
    assert result
    return result


@pytest.fixture(scope='module')
def parsed_cases():
    results = {}
    for case in CASES:
        path = FIXTURES / case['filename']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case['fixture_sha256']
        artifact = PDFParser(None).build_parse_artifact(str(path))
        results[case['doc_id']] = artifact, _context(artifact)
    return results


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['doc_id'])
def test_public_pdf_and_facts_keep_all_source_identities_and_amounts(parsed_cases, case):
    artifact, ctx = parsed_cases[case['doc_id']]
    physical = {f'{table.fragment_uid}:r{cell.row_idx}c{cell.col_idx}': cell
                for table in artifact.physical_fragments for cell in table.cells}
    assert len(ctx.facts) == len(case['expected'])
    for expected in case['expected']:
        identity = expected['expected']
        facts = [fact for fact in ctx.facts if fact.subject_code == identity['subject_code']
                 and normalize_text(fact.subject_name) == normalize_text(identity['subject_name'])
                 and fact.indicator == identity['indicator']
                 and fact.extra_dims['evidence_refs'][0]['physical_page'] == expected['page']]
        assert len(facts) == 1, expected['truth_id']
        fact = facts[0]
        assert fact.raw_value == expected['raw_text'], expected['truth_id']
        assert operand_rejection_reason(fact, '固定原件') is None
        ref = fact.extra_dims['evidence_refs'][0]
        original = physical[ref['source_cell_id']]
        assert original.raw_text == fact.raw_value and list(original.bbox) == ref['bbox']
        assert ref['file_hash'] == artifact.source_file_hash
    receipt = build_table_import_receipt(artifact, ctx.facts, org_name='固定原件')
    assert receipt['fact_missing_count'] == receipt['fact_conflict_count'] == 0
    assert receipt['unresolved_money_cell_count'] == 0
    assert receipt['original_pdf_full_coverage_verified'] is False


def test_header_contamination_has_independent_contiguous_source_and_exclusion_proof(parsed_cases):
    artifact, ctx = parsed_cases['SH2024NEW_02']
    repaired = [fact for fact in ctx.facts if fact.subject_code in {'205', '20503', '2050302'}]
    assert len(repaired) == 39
    with pdfplumber.open(FIXTURES / 'SH2024NEW_02_identity.pdf') as pdf:
        for fact in repaired:
            sources = fact.extra_dims['row_identity_sources']
            names = [source for source in sources if source['recovered_text'] == fact.subject_name]
            assert len(names) == 1 and names[0]['grid_raw_text'] != fact.subject_name
            proofs = names[0]['native_text_sources']
            assert ''.join(proof['text'] for proof in proofs) == fact.subject_name
            exclusions = [excluded for proof in proofs for excluded in proof['excluded_header_sources']]
            assert exclusions and all(excluded['anchor_grid_text'] == '合计' for excluded in exclusions)
            for proof in [*proofs, *exclusions]:
                chars = pdf.pages[proof['physical_page'] - 1].chars
                assert ''.join(char['text'] for char in chars[proof['start_char_index']:proof['end_char_index']]) == proof['text']
    # 物理原网格仍可回读，不能把修复结果反写成原页抽取基线。
    assert any('款' in str(row) for table in artifact.source_grids for row in table.rows)


def test_economic_short_header_covers_codes_zero_and_footer_totals(parsed_cases):
    _, ctx = parsed_cases['SH2024NEW_06']
    code_facts = [fact for fact in ctx.facts if fact.subject_code != 'total']
    assert len(code_facts) == 65 and all(fact.subject_code for fact in code_facts)
    assert any(fact.raw_value == '0.00' for fact in code_facts)
    totals = {fact.subject_name: fact.raw_value for fact in ctx.facts if fact.subject_code == 'total'}
    assert totals == {'人员经费合计': '37,896.38', '公用经费合计': '11,056.12'}


def _economic_pdf(path, *, name_header='科目名称', code='30101', name='基本工资'):
    from test_native_structure_pdf_pipeline import lines, text
    with pymupdf.open() as doc:
        page = doc.new_page(width=700, height=400)
        text(page, 40, 60, '一般公共预算财政拨款基本支出决算表')
        text(page, 40, 80, '单位：万元')
        xs, ys = [40, 180, 420, 600], [100, 140, 180, 220]
        lines(page, xs, ys)
        for y, values in [(125, ['经济分类科目', name_header, '决算数']),
                          (165, [code, name, '10.00']), (205, ['合计', '', '10.00'])]:
            for col, value in enumerate(values):
                if value:
                    text(page, xs[col] + 5, y, value)
        doc.save(path)


def test_public_facts_reject_missing_code_when_literal_dimension_requires_it(tmp_path):
    path = tmp_path / 'missing_code.pdf'
    _economic_pdf(path, code='')
    ctx = _context(PDFParser(None).build_parse_artifact(str(path)))
    fact = next(fact for fact in ctx.facts if fact.subject_name == '基本工资')
    assert fact.subject_code is None
    assert fact.extra_dims['row_identity_status'] == 'missing_subject_code'
    assert operand_rejection_reason(fact, '固定原件') is not None
    assert operand_rejection_reason(next(fact for fact in ctx.facts if fact.subject_code == 'total'), '固定原件') is None


def test_short_economic_heading_does_not_claim_code_without_adjacent_name_header(tmp_path):
    from src.services.fiscal_column_identity import code_dimension_columns
    path = tmp_path / 'unknown_dimension.pdf'
    _economic_pdf(path, name_header='支出事项')
    artifact = PDFParser(None).build_parse_artifact(str(path))
    assert artifact.table_fragments
    assert all(not code_dimension_columns(table) for table in artifact.table_fragments)


@pytest.mark.parametrize('name', ['其他项目支出', '借款服务支出', '类别事务支出'])
def test_native_names_keep_legitimate_header_marker_characters(tmp_path, name):
    path = tmp_path / 'legitimate_name.pdf'
    _economic_pdf(path, name=name)
    ctx = _context(PDFParser(None).build_parse_artifact(str(path)))
    assert any(fact.subject_name == name and fact.subject_code == '30101' for fact in ctx.facts)


def test_header_flow_is_not_excluded_without_original_contiguous_marker_proof(monkeypatch):
    original = PDFParser._extract_tables_from_page

    def damaged(self, page, page_number, continuation=None):
        if page_number == 1:
            chars = page.chars
            # 改坏证明中的一字，模拟无法证实为分类标记的原生字流；不修改PDF文件。
            chars[65] = {**chars[65], 'text': '政'}
        return original(self, page, page_number, continuation)

    monkeypatch.setattr(PDFParser, '_extract_tables_from_page', damaged)
    artifact = PDFParser(None).build_parse_artifact(str(FIXTURES / 'SH2024NEW_02_identity.pdf'))
    ctx = _context(artifact)
    changed = [fact for fact in ctx.facts if fact.subject_code == '205'
               and fact.extra_dims['evidence_refs'][0]['physical_page'] == 1]
    assert changed and all(fact.subject_name != '教育支出' for fact in changed)
    assert all(not any(proof.get('excluded_header_sources') for source in fact.extra_dims['row_identity_sources']
                       for proof in source['native_text_sources']) for fact in changed)


@pytest.mark.parametrize('fault', [None, 'native_order_interrupted', 'native_order_interrupted_many', 'tail_not_right_aligned'])
def test_single_tail_has_no_other_reading_gap_to_hide_unsafe_admission(fault):
    # 单个合成金额足以检验拒绝分支，不能依靠同表其他缺格碰巧阻断错误前缀。
    path = FIXTURES.parent / 'native_tail_v25/unsafe_single_tail.pdf'
    meta = json.loads(path.with_suffix('.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == meta['fixture_sha256']
    with pdfplumber.open(path) as pdf:
        chars = pdf.pages[0].chars
        index = next(index for index, char in enumerate(chars)
                     if char['text'] == '5' and abs(char['x0'] - meta['tail_x']) < .1)
        if fault in {'native_order_interrupted', 'native_order_interrupted_many'}:
            chars[index:index] = [{**chars[index - 1], 'text': ' '}
                                  for _ in range(4 if fault.endswith('_many') else 1)]
        elif fault == 'tail_not_right_aligned':
            chars[index]['x0'] -= 8
            chars[index]['x1'] -= 8
        artifact = PDFParser(None).build_parse_artifact_from_handle(pdf, file_hash=meta['fixture_sha256'])
    ctx = _context(artifact)
    facts = [fact for fact in ctx.facts if fact.subject_code == meta['subject_code']
             and fact.indicator == meta['indicator']]
    receipt = build_table_import_receipt(artifact, ctx.facts, org_name='固定原件')
    if fault is None:
        assert len(facts) == 1 and facts[0].raw_value == meta['expected_raw_value']
        assert facts[0].recognition_status=='ambiguous'
        assert operand_rejection_reason(facts[0], '固定原件') is not None
        assert receipt['unresolved_money_cell_count']>0
    else:
        assert not ctx.definite_facts()
        assert not facts or operand_rejection_reason(facts[0], '固定原件') is not None
        assert any(gap['reason'] == 'native_decimal_tail_unresolved' for gap in receipt['reading_quality']['gaps'])


def test_merged_rows_and_cross_grid_tail_do_not_invent_source_grid_rows():
    path = FIXTURES.parent / 'native_tail_v25/unsafe_split_tail.pdf'
    meta = json.loads(path.with_suffix('.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == meta['fixture_sha256']
    artifact = PDFParser(None).build_parse_artifact(str(path))
    ctx = _context(artifact)
    # 两种恢复无法共同保住唯一原格身份时，组合表必须继续拒绝确定性计算。
    assert not ctx.definite_facts()
    receipt = build_table_import_receipt(artifact, ctx.facts, org_name='固定原件')
    assert receipt['reading_quality']['gaps'] or receipt['unresolved_money_cell_count'] > 0
    assert any(proof.get('reason') == 'combined_merged_rows_and_cross_grid_tail_not_supported'
               for table in artifact.physical_fragments for cell in table.cells
               for proof in cell.native_text_sources)
    for table in artifact.physical_fragments:
        originals = [grid for grid in artifact.source_grids if grid.fragment_uid == table.fragment_uid]
        assert len(originals) == 1 and table.rows == originals[0].rows
        assert [(cell.row_idx, cell.col_idx, cell.raw_text, cell.bbox) for cell in table.cells] == [
            (cell.row_idx, cell.col_idx, cell.raw_text, cell.bbox) for cell in originals[0].cells]
    grids = {f'{table.fragment_uid}:r{cell.row_idx}c{cell.col_idx}': cell
             for table in artifact.source_grids for cell in table.cells}
    for table in artifact.physical_fragments:
        for cell in table.cells:
            for proof in cell.native_text_sources:
                if proof.get('source_grid_fragment_uid'):
                    cid = (f"{proof['source_grid_fragment_uid']}:"
                           f"r{proof['source_grid_row']}c{proof['source_grid_col']}")
                    assert list(grids[cid].bbox) == proof['source_grid_bbox']
    for fact in ctx.facts:
        for ref in fact.extra_dims['evidence_refs']:
            proofs = ref.get('source_provenance', {}).get('native_text_sources', [])
            for proof in proofs:
                if proof.get('recovery_kind') != 'cross_grid_decimal_tail':
                    continue
                original = grids[ref['source_cell_id']]
                assert original.raw_text == ref['source_provenance']['grid_raw_text']
                assert list(original.bbox) == proof['source_grid_bbox']
