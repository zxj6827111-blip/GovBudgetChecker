from src.engine.checklist_identity_rules import CMM017_OrgNameConsistency, name_mentions
from src.engine.rules_v33 import build_document


OWNER='上海市普陀区石泉路街道经济发展服务中心'
WRONG='上海市普陀区人民政府石泉路街道经济发展服务中心'


def document(body):
    return build_document('source.pdf',[
        OWNER+'2025年度单位决算',
        '第一部分'+OWNER+'概况\n编制单位：'+OWNER,
        '第三部分'+OWNER+'2025年度决算情况说明\n'+body],[],0)


def test_year_wrapping_does_not_hide_explicit_financial_owner_conflict():
    doc=document(WRONG+' 2025\n年度收入支出总计302.86万元。\n'+
                 WRONG+' 2025\n年度财政拨款收入支出总计302.86万元。')
    found=CMM017_OrgNameConsistency().apply(doc)
    assert len(found)==1 and found[0].location['actual']==WRONG
    assert found[0].location['expected']==OWNER
    assert found[0].location['page']==3


def test_other_org_in_citation_or_unrelated_sentence_is_not_a_self_declaration():
    doc=document('例如：'+WRONG+'2025年度收入支出总计302.86万元。\n'+
                 '根据'+WRONG+'2025年度收入支出总计302.86万元。')
    assert not CMM017_OrgNameConsistency().apply(doc)


def test_consistent_declaration_does_not_create_a_problem():
    assert not CMM017_OrgNameConsistency().apply(document(OWNER+'2025\n年度收入支出总计302.86万元。'))
