"""独立合同反例：提取器读数异常不能自动证明原件金额印错。"""
import sys
from pathlib import Path
CANDIDATE = Path("/Volumes/workSSD/software development/gbc-worktrees/real-painpoint-fixes-20261104")
sys.path.insert(0, str(CANDIDATE))
from src.services import structured_ingest_runner as module
assert Path(module.__file__).resolve() == CANDIDATE / "src/services/structured_ingest_runner.py"
_reading_review_items = module._reading_review_items


def quality(raw, reason='fact_unbound'):
    return {'gaps': [], 'mapping_incomplete_tables': [{
        'fragment_uid':'table-1','table_code':'FIN_03_expenditure','title':'支出决算表','page':8,
        'unresolved_money_cell_count':1,'reasons':{reason:1},'arithmetic_conflicts':[]}],
        'unbound_money_cells':[{'source_cell_id':'table-1:r0c3','fragment_uid':'table-1',
        'table_code':'FIN_03_expenditure','raw_text':raw,'page':9,'bbox':[10,20,80,40],'reason':reason}]}


def test_two_extracted_amounts_are_not_proof_of_original_double_decimal_typo():
    items=_reading_review_items(quality('1.00\n2.00'))
    assert not any(i['type']=='invalid_number_format' for i in items)
    assert any(i['type']=='table_mapping_incomplete' for i in items)


def test_explicit_fact_binding_conflict_survives_format_classification():
    items=_reading_review_items(quality('2.359.46','fact_binding_conflict'))
    assert any(i['type']=='table_mapping_incomplete' for i in items)


def test_original_single_lexeme_double_decimal_keeps_location_and_raw():
    items=_reading_review_items(quality('2.359.46'))
    formats=[i for i in items if i['type']=='invalid_number_format']
    assert len(formats)==1
    assert formats[0]['page_number']==9
    assert formats[0]['cells'][0]['raw_text']=='2.359.46'


def test_normal_money_preserves_unbound_mapping():
    items=_reading_review_items(quality('1,860.33'))
    assert [i['type'] for i in items]==['table_mapping_incomplete']
