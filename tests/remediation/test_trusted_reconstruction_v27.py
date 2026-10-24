"""冻结真实原件反例，包含保持报错的差额变体。"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pdfplumber
import pytest

from src.engine.fact_context import FactContext, build_fact_context_from_artifact, resolve_operand
from src.engine.rules_v33 import R33005_TableTotalConsistency, build_document
from src.schemas.parse_artifact import ParseArtifact, ParsedCell
from src.services.fiscal_code_hierarchy import hierarchy_paths, leaf_codes
from src.services.pdf_parser import PDFParser
from src.services.source_reconstruction import typed_literal

FIXTURES = Path(__file__).parents[1] / 'fixtures/native_trusted_v27'


@pytest.mark.parametrize('name,page,code', [
    ('research23',16,'FIN_09_state_capital'), ('research24',16,'FIN_09_state_capital'),
    ('finance24',15,'FIN_08_gov_fund')])
def test_one_physical_empty_grid_has_one_business_instance(name,page,code):
    with pdfplumber.open(FIXTURES / (name + '.pdf')) as pdf:
        parser = PDFParser(None)
        tables = parser._extract_tables_from_page(pdf.pages[page-1], page)
    assert len([t for t in tables if t.table_code == code]) == 1
    assert parser._reconstruction_decisions[0]['status'] == 'duplicate_projection'
    # 被排除的是候选投影，原始物理网格和其他策略仍有记录。
    assert parser._source_grid_fragments
    assert any(t.extraction_method == 'text' for t in parser._cell_candidate_tables)


def test_real_bilateral_open_bottom_zero_retains_literal_and_word_sources():
    with pdfplumber.open(FIXTURES / 'commerce25.pdf') as pdf:
        table, = PDFParser(None)._extract_tables_from_page(pdf.pages[17],18)
    assert table.rows[-1] == ['', '', '', '', '310', '22', '无形资产购置', '0.00']
    cell = next(c for c in table.cells if c.row_idx == len(table.rows)-1 and c.col_idx == 7)
    assert cell.normalized_value_wan == Decimal('0.00')
    assert cell.bbox and cell.page_number == 18 and cell.native_text_sources
    assert all(not c.native_blank_verified for c in table.cells if c.row_idx == len(table.rows)-1 and c.col_idx < 4)


def test_empty_grid_literal_survives_complete_json_roundtrip():
    cell = ParsedCell(0,0,'','',None,1,(0,0,10,10),False,.9,'万元','lines',grid_raw_text='')
    assert ParsedCell.from_dict(json.loads(json.dumps(cell.to_dict()))).grid_raw_text == ''


def test_padded_hierarchy_is_interpreted_with_current_table_format():
    codes = ['2130000','2130100','2130105']
    assert hierarchy_paths(codes) == dict(zip(codes,['213','21301','2130105']))
    assert leaf_codes(codes) == {'2130105'}
    assert leaf_codes(['2130100']) == {'2130100'}


@pytest.fixture(scope='module')
def farm():
    path = FIXTURES / 'farm23.pdf'
    artifact = PDFParser(None).build_parse_artifact(str(path))
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text() or '' for p in pdf.pages]
    tables = [[] for _ in texts]
    for table in artifact.physical_fragments:
        tables[table.page_number-1].append(table.rows)
    doc = build_document(str(path),texts,tables,path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = build_fact_context_from_artifact(artifact,org_name='上海市农场管理局',fiscal_year=2023,report_kind='final')
    return doc


def test_real_farm_parent_and_child_amounts_are_not_added_twice(farm):
    assert R33005_TableTotalConsistency().apply(farm) == []
    assert any(r['status'] == 'pass' for r in farm.fact_receipts)


def test_continued_two_panel_total_is_not_added_to_current_year_subtotal():
    data = json.loads((Path(__file__).parents[1]/'fixtures/cross_san_gong_truth_page_data.json').read_text())
    table = data['page_tables'][17][0]
    issues,contradictions,counted=R33005_TableTotalConsistency()._check_one_table(table,'P18',18,{})
    assert not issues and not contradictions and counted is False


def test_farm_actual_wrong_leaf_is_still_reported(farm):
    doc = deepcopy(farm)
    leaf = next(f for f in doc.fact_context.facts if f.table_code == 'FIN_02_income'
                and f.subject_code == '2130105' and f.indicator == 'total_actual')
    leaf.normalized_value += Decimal('100')
    doc.fact_context.version_token = doc.fact_context.compute_version_token()
    assert R33005_TableTotalConsistency().apply(doc)


def test_income_certified_blank_executes_and_preserves_raw_snapshot(farm):
    from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck
    doc = deepcopy(farm)
    before = doc.fact_context.to_dict()
    assert R33240_Table2_IncomeAdvancedCheck().apply(doc) == []
    assert doc.fact_context.to_dict() == before
    receipts = [r for r in doc.fact_receipts if r.get('cell_observations') and r['rule_id'] == 'V33-240']
    assert receipts and all(r['status'] == 'pass' for r in receipts)


def test_income_nonzero_difference_is_not_hidden_by_blank_terms(farm):
    from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck
    doc = deepcopy(farm)
    total = next(f for f in doc.fact_context.facts if f.table_code == 'FIN_02_income'
                 and f.subject_code == 'total' and f.indicator == 'total_actual')
    total.normalized_value += Decimal('100')
    assert R33240_Table2_IncomeAdvancedCheck().apply(doc)


def test_same_amount_different_business_identity_remains_ambiguous(farm):
    first = deepcopy(farm.fact_context.facts[0]); second = deepcopy(first)
    first.subject_code, second.subject_code = '2130105','2130106'
    first.subject_name, second.subject_name = '甲','乙'
    first.fact_uid, second.fact_uid = 'a','b'
    context = FactContext([first,second],farm.fact_context.file_hash,farm.fact_context.parse_run_uid,
                          org_name=farm.fact_context.org_name)
    assert resolve_operand(context,first.indicator).status == 'blocked'
    before = context.compute_version_token()
    first.subject_code = '2130107'
    assert context.compute_version_token() != before


@pytest.mark.parametrize('raw,kind,value', [
    ('(1,234.50)','amount','-1234.50'), ('9007199254740993.01','amount','9007199254740993.01'),
    ('0.00','zero','0.00'), ('—','dash',None), ('','blank',None), ('12 34','unrecognized',None),
    ('1.25%','percent','1.25')])
def test_typed_literals_do_not_round_or_convert_unknown_to_zero(raw,kind,value):
    assert typed_literal(raw,monetary=True) == (kind,value)


@pytest.fixture(scope='module')
def chemical():
    path = FIXTURES / 'chemical23.pdf'
    artifact = PDFParser(None).build_parse_artifact(str(path))
    with pdfplumber.open(path) as pdf: texts = [p.extract_text() or '' for p in pdf.pages]
    doc = build_document(str(path), texts, [], path.stat().st_size)
    doc.parse_artifact = artifact
    doc.fact_context = build_fact_context_from_artifact(artifact, org_name='上海化学工业区管理委员会',
                                                        fiscal_year=2023,report_kind='final')
    return doc


def chemical_item(doc):
    from src.engine.native_final_disclosure import _general_ranges, _ITEM
    text,ranges = _general_ranges(doc)
    _,start,end = ranges[0]
    items = list(_ITEM.finditer(text.compact,start,end))
    return next(i for i in items if i['xiang']=='机关事业单位基本养老保险缴费支出'),items


def test_real_truncated_names_have_local_evidence_and_keep_real_conflict(chemical):
    from src.engine.rules_v33 import R33222_Narrative5_T5
    from src.engine.rule_outcome import RuleDeferred
    doc = deepcopy(chemical)
    original = doc.fact_context.to_dict()
    with pytest.raises(RuleDeferred) as signal:
        R33222_Narrative5_T5().apply(doc)
    # 另一条原文类名与原表不一致仍需复核，不能把整条规则改成通过。
    assert '资源勘探信息等支出' in str(signal.value)
    assert doc.fact_context.to_dict()==original
    aliases=[(r,s) for r in doc.fact_receipts for s in r.get('disclosure_sources',[])
             if s.get('basis')=='unique_native_code_parent_and_narrative_prefix']
    assert len(aliases)==3
    assert all(r['status']=='pass' and s['page']==12 for r,s in aliases)
    assert any(s['raw_subject_name']=='其他行政事业单位养老支' for _,s in aliases)


@pytest.mark.parametrize('fault',['ambiguous_prefix','wrong_parent','wrong_year','missing_physical_name'])
def test_truncated_name_never_uses_unproven_alias(chemical,fault):
    from src.engine.native_final_disclosure import _hierarchy_fact
    doc=deepcopy(chemical); item,items=chemical_item(doc)
    facts=[f for f in doc.fact_context.facts if f.table_code=='FIN_05_general_public_expenditure']
    target=next(f for f in facts if f.subject_code=='2080505' and f.indicator=='total_actual')
    if fault=='ambiguous_prefix':
        other=deepcopy(target);other.subject_code='2080507';other.fact_uid+=':other';facts.append(other)
        table=next(t for t in doc.parse_artifact.table_fragments if t.table_code==target.table_code)
        copied=[deepcopy(c) for c in table.cells if c.row_idx==target.row_order]
        for c in copied:
            c.row_idx+=100
            if c.raw_text=='2080505':c.raw_text='2080507'
        other.row_order+=100;table.cells.extend(copied)
    elif fault=='wrong_parent':target.subject_code='2080605'
    elif fault=='wrong_year':target.fiscal_year=2022
    else:
        for t in doc.parse_artifact.table_fragments:
            for c in t.cells:
                if c.raw_text==target.subject_name:c.raw_text='原件名称冲突'
    aliases=[]
    fact,error=_hierarchy_fact(doc,facts,item,siblings=items,aliases=aliases)
    assert fact is None and error and not aliases


def test_truncated_name_binding_still_checks_actual_amount(chemical):
    from src.engine.rules_v33 import R33222_Narrative5_T5
    from src.engine.rule_outcome import RuleDeferred
    doc=deepcopy(chemical)
    target=next(f for f in doc.fact_context.facts if f.table_code=='FIN_05_general_public_expenditure'
                and f.subject_code=='2080505' and f.indicator=='total_actual')
    target.normalized_value+=Decimal('10')
    with pytest.raises(RuleDeferred) as signal:R33222_Narrative5_T5().apply(doc)
    assert any(target.fact_uid in issue.fact_ids for issue in signal.value.partial_issues)
