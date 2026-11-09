from copy import deepcopy

from src.engine.native_hierarchy_checks import check_native_detail_tables
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.rules_v33 import R33241_Table3_ExpenseAdvancedCheck
from tests.remediation.test_shiquan_native_empty_rows import grid_document


def case():
    rows=[['功能分类科目编码','科目名称','本年支出合计','基本支出','项目支出','上缴上级支出','经营支出','对附属单位补助支出'],
          ['合计','','10.00','','3.00','','',''],
          ['201','一般公共服务支出','3.00','','3.00','','',''],
          ['208','社会保障和就业支出','7.00','7.00','','','','']]
    doc=grid_document(rows,'FIN_03_expenditure')
    doc.page_texts=['甲局2025年度部门决算。'+'\n'.join(' '.join(row) for row in rows)]
    issues=check_native_detail_tables(R33241_Table3_ExpenseAdvancedCheck(),doc,table_codes=['FIN_03_expenditure'])
    one=next(i for i in issues if i.location.get('code')=='total' and i.location.get('column')=='basic_actual')
    two=deepcopy(one);two.rule='V33-005'
    return doc,one,two


def test_certified_blank_parent_and_numeric_child_are_one_formula_not_multiple_findings():
    doc,one,two=case()
    assert len(one.location['operand_refs'])==1
    assert any(x['role']=='parent' for x in one.location['native_blank_operands'])
    grouped=deduplicate_native_issues([one,two],doc)
    assert len(grouped)==1
    assert set(grouped[0].location['related_rule_ids'])=={'V33-241','V33-005'}


def test_changed_blank_role_or_missing_observation_does_not_merge():
    doc,one,two=case();two.location['native_blank_operands'][0]['role']='child'
    assert len(deduplicate_native_issues([one,two],doc))==2
    doc,one,two=case();two.location['native_blank_operands'].pop()
    assert len(deduplicate_native_issues([one,two],doc))==2
    doc,one,two=case();two.location['native_blank_sources']=[]
    assert len(deduplicate_native_issues([one,two],doc))==2
