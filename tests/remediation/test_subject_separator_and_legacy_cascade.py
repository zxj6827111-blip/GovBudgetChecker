from types import SimpleNamespace

import pytest

from src.engine.native_final_disclosure import _ITEM
from src.services.obligation_finding_coverage import cascade_root_issue_id


@pytest.mark.parametrize('separator', ['', '-', '—', '–'])
def test_separator_is_grammar_not_part_of_fiscal_subject(separator):
    source=f'一般公共服务支出(类){separator}发展与改革事务(款){separator}其他发展与改革事务支出(项)'
    match=_ITEM.search(source)
    assert match and match.groupdict()==dict(klass='一般公共服务支出',kuan='发展与改革事务',xiang='其他发展与改革事务支出')


@pytest.mark.parametrize('shape',['dual','legacy_all','legacy_buckets'])
def test_same_missing_table_cascade_for_all_current_storage_shapes(shape):
    issue=dict(id='missing-t3',rule_id='V33-002',severity='error',message='缺失表：支出决算表')
    result={'rule_findings':[issue]} if shape=='dual' else {'issues':{'all':[issue]}} if shape=='legacy_all' else {'issues':{'error':[issue],'warn':[]}}
    obligation=SimpleNamespace(status='insufficient_data',detail='支出决算表(T3)缺失',depends_on=['FIN_03_expenditure'],blocked_by=[])
    assert cascade_root_issue_id(obligation,result,{'missing-t3'})=='missing-t3'
    assert cascade_root_issue_id(obligation,result,set()) is None
    obligation.detail+='；说明中的项“行政运行”在表内找不到同名科目'
    assert cascade_root_issue_id(obligation,result,{'missing-t3'}) is None
