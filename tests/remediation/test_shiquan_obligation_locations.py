"""已定位的原文缺口优先，不把表首或其他子检查的早页当作当前卡点。"""
from types import SimpleNamespace

from src.services.obligation_locations import resolve_obligation_locations


def test_text_conflict_pages_precede_unrelated_early_checker_and_table_locations():
    item = SimpleNamespace(obligation_id='OBL-TXT-T5', title='一般公共预算支出决算表',
        basis='', detail='说明中的项“统计信息事务”无法对应', checkers=['V33-222'],
        missing_operands=[{'page':p,'source':'text','role':'说明科目对应金额'} for p in [29,32,36]])
    ingest={'reading_import':{'tables':[{'table_code':'FIN_05_general_public_expenditure',
        'page':19,'title':'一般公共预算财政拨款支出决算表'}]}}
    result={'rule_findings':[{'rule_id':'V33-222','evidence':[{'page':7,'quote':'其他关系'}]}]}
    meta={'structure_view':{'page_texts':['统计信息事务']+['']*43}}
    locations=resolve_obligation_locations(item,ingest,result,meta)
    assert [x.page for x in locations][:3]==[29,32,36]
    assert {7,19}.issubset({x.page for x in locations}), '关联原表与其他证据仍保留'


def test_invalid_structured_page_cannot_replace_current_document_location():
    item=SimpleNamespace(obligation_id='OBL-TXT-T5',title='一般公共预算支出决算表',
        basis='',detail='',checkers=[],missing_operands=[{'page':999,'role':'错代际'},
                                                        {'page':True,'role':'非法页'}])
    ingest={'reading_import':{'tables':[{'table_code':'FIN_05_general_public_expenditure',
        'page':19,'title':'一般公共预算财政拨款支出决算表'}]}}
    locations=resolve_obligation_locations(item,ingest,None,{'pages':44})
    assert [x.page for x in locations]==[19]
