"""原件错码/缺上级不能把项级金额重复放进类级合计。"""
from copy import deepcopy

import pytest

from src.engine.native_hierarchy_checks import check_native_detail_tables
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33005_TableTotalConsistency, R33120_DetailTableCheck
from tests.remediation.test_real_intake_table_gaps import T5, native_doc


def check(doc, rule=None):
    return check_native_detail_tables(rule or R33005_TableTotalConsistency(), doc, table_codes=[T5])


def mirror_document():
    from src.engine.fact_context import build_fact_context_from_artifact
    from tests.remediation.test_shiquan_native_empty_rows import grid_document
    rows = [['功能分类科目编码','科目名称','合计'],['合计','','100'],
            ['201','一般公共服务支出','100'],['20132','组织事务','100'],
            ['3013299','其他组织事务支出','100']]
    doc = grid_document(rows, T5)
    mirror = grid_document([['功能分类科目编码','科目名称','本年收入合计'],['合计','','100'],
                           ['201','一般公共服务支出','100'],['20132','组织事务','100'],
                           ['2013299','其他组织事务支出','100']], 'FIN_02_income')
    add_native_table(doc,mirror,'mirror',2)
    return doc


def add_native_table(doc,other,prefix,page):
    from src.engine.fact_context import build_fact_context_from_artifact
    for table in [*other.parse_artifact.table_fragments,*other.parse_artifact.physical_fragments]:
        table.fragment_uid=f'{prefix}-fragment'
        table.page_number=page
        for cell in table.cells:cell.page_number=page
    instance=other.parse_artifact.logical_tables[0]
    instance.instance_uid=f'{prefix}-instance'
    instance.fragment_uids=[f'{prefix}-fragment']
    instance.start_page=instance.end_page=page
    doc.parse_artifact.table_fragments+=other.parse_artifact.table_fragments
    doc.parse_artifact.physical_fragments+=other.parse_artifact.physical_fragments
    doc.parse_artifact.logical_tables+=other.parse_artifact.logical_tables
    doc.fact_context=build_fact_context_from_artifact(doc.parse_artifact,org_name='甲局',fiscal_year=2025,
                                                     report_kind='final',doc_type='dept_final')
    # 模拟原页文字层；认证器仍独立核验哈希、原格、页码、坐标及证据ID。
    doc.page_texts=['']*page
    for table in doc.parse_artifact.physical_fragments:
        doc.page_texts[table.page_number-1]+='\n'.join(' '.join(str(v or '') for v in row) for row in table.rows)


def test_mistyped_item_requires_complete_native_mirror_chain_and_never_changes_original_code():
    doc = mirror_document()
    before=deepcopy(doc.fact_context.to_dict())
    issues = check(doc)
    assert len(issues) == 1 and issues[0].location['check_type']=='orphan_classification'
    assert issues[0].location['orphan_codes']==['3013299']
    assert issues[0].location['classification_mirrors'][0]['mirror_code']=='2013299'
    assert {ref['subject_code'] for ref in issues[0].location['operand_refs']}=={'201','20132','3013299','2013299'}
    total=next(r for r in doc.fact_receipts if r['sub_check_id']==f'{T5}:total:total_actual')
    assert total['status']=='pass'
    assert doc.fact_context.to_dict()==before


def test_mirror_code_admission_does_not_depend_on_matching_money():
    from decimal import Decimal
    doc=mirror_document()
    mirror=next(f for f in doc.fact_context.facts if f.table_code=='FIN_02_income' and f.subject_code=='2013299')
    # 改的是另一表的当前金额；本表复算仍只用本表100，禁止跨表借数或按同金额配科目。
    mirror.normalized_value=Decimal('123')
    issues=check(doc)
    assert len(issues)==1 and issues[0].location['check_type']=='orphan_classification'
    total=next(r for r in doc.fact_receipts if r['sub_check_id']==f'{T5}:total:total_actual')
    assert total['status']=='pass' and mirror.fact_uid not in total['operand_fact_ids']


@pytest.mark.parametrize('fault',['no_mirror','other_name','other_year','other_owner','missing_parent','other_stage','bad_cell','ambiguous_mirror','missing_native_text','wrong_evidence_id','ocr_table','ocr_cell','ocr_evidence'])
def test_mixed_depth_rows_are_not_excluded_without_unique_current_original_mirror(fault):
    doc=mirror_document()
    mirror=[f for f in doc.fact_context.facts if f.table_code=='FIN_02_income' and f.subject_code=='2013299'][0]
    if fault=='no_mirror':doc.fact_context.facts=[f for f in doc.fact_context.facts if f.table_code!=mirror.table_code]
    elif fault=='other_name':mirror.subject_name='其他科目'
    elif fault=='other_year':mirror.fiscal_year=2024
    elif fault=='other_owner':mirror.org_name='乙局'
    elif fault=='missing_parent':doc.fact_context.facts=[f for f in doc.fact_context.facts if not(f.table_code==mirror.table_code and f.subject_code=='20132')]
    elif fault=='other_stage':mirror.budget_stage='预算'
    elif fault=='bad_cell':mirror.extra_dims['evidence_refs'][0]['source_cell_id']='old-cell'
    elif fault=='missing_native_text':doc.page_texts[1]='无本表金额原文'
    elif fault=='wrong_evidence_id':mirror.extra_dims['evidence_refs'][0]['evidence_id']='forged'
    elif fault=='ocr_evidence':mirror.extra_dims['evidence_refs'][0]['source_method']='ocr'
    elif fault in {'ocr_table','ocr_cell'}:
        table=next(t for t in doc.parse_artifact.table_fragments if t.table_code==mirror.table_code)
        if fault=='ocr_table':table.extraction_method='ocr'
        else:
            for cell in table.cells:
                if cell.col_idx<2:cell.extraction_method='ocr'
    else:
        candidate=deepcopy(mirror)
        candidate.fact_uid='different-code'
        candidate.subject_code='2043299'
        doc.fact_context.facts.append(candidate)
    issues=check(doc)
    assert not any(i.location.get('check_type')=='orphan_classification' for i in issues)
    total=next(r for r in doc.fact_receipts if r['sub_check_id']==f'{T5}:total:total_actual')
    leaf=[f for f in doc.fact_context.facts if f.table_code==T5 and f.subject_code=='3013299'][0]
    assert leaf.fact_uid in total['operand_fact_ids']


def test_unequal_mixed_depth_total_without_source_mirror_is_not_silently_balanced():
    doc=native_doc([('total','90',1,'万元'),('201','100',1,'万元'),('3013299','10',1,'万元')])
    issue,=check(doc)
    assert issue.location['code']=='total' and '20.00' in issue.message
    assert [ref['subject_code'] for ref in issue.location['operand_refs']]==['total','201','3013299']


def test_uniform_item_only_disclosure_can_sum_without_inventing_class_rows():
    doc = native_doc([('total', '100', 1, '万元'), ('2013299', '100', 1, '万元')])
    assert check(doc) == []
    total = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:total:total_actual')
    assert total['status'] == 'pass' and total['operand_fact_ids'] == ['fact-1', 'fact-2']


def test_missing_operand_in_item_only_table_cannot_pass():
    doc = native_doc([('total', '100', 1, '万元'), ('2013299', '100', 1, '万元')])
    doc.fact_context.facts.pop()
    with pytest.raises(RuleDeferred) as result:
        check(doc)
    assert not result.value.partial_issues
    total = next(r for r in doc.fact_receipts if r['sub_check_id'] == f'{T5}:total:total_actual')
    assert total['status'] == 'insufficient_data'


@pytest.mark.parametrize('codes', [('201', '20132', '2013299'), ('2010000', '2013200', '2013299')])
def test_valid_native_hierarchy_and_proven_padded_class_codes_remain_unchanged(codes):
    doc = native_doc([('total', '100', 1, '万元'), *[(code, '100', 1, '万元') for code in codes]])
    assert check(doc) == []


def test_detail_amount_checker_uses_proven_source_chain_without_changing_fact_codes():
    doc=mirror_document()
    assert check(doc,R33120_DetailTableCheck())==[]
    total=next(r for r in doc.fact_receipts if r['sub_check_id']==f'{T5}:total:total_actual')
    leaf=[f for f in doc.fact_context.facts if f.table_code==T5 and f.subject_code=='3013299'][0]
    assert leaf.fact_uid not in total['operand_fact_ids']


def orphan_pair():
    from dataclasses import asdict
    from tests.remediation.test_shiquan_native_empty_rows import grid_document
    doc=mirror_document()
    other=grid_document([['功能分类科目编码','科目名称','本年支出合计'],['合计','','100'],
                         ['201','一般公共服务支出','100'],['20132','组织事务','100'],
                         ['3013299','其他组织事务支出','100']],'FIN_03_expenditure')
    add_native_table(doc,other,'third',3)
    issues=check_native_detail_tables(R33005_TableTotalConsistency(),doc,table_codes=[T5,'FIN_03_expenditure'])
    rows=[asdict(issue) for issue in issues]
    assert len(rows)==2
    for index,row in enumerate(rows):
        row['rule_id']='V33-005'
        row['id']=f'orphan-{index}'
    return doc,rows


def test_mirrored_orphan_code_merges_but_keeps_both_source_rows_and_failure_receipts():
    from src.engine.native_issue_dedup import deduplicate_native_issues
    doc, issues = orphan_pair()
    receipts=[dict(rule_id='V33-005',sub_check_id=item['check_relation_id'].split(':',1)[1],
                   status='fail',issue_refs=[item['id']]) for item in issues]
    result = deduplicate_native_issues(issues, doc, receipts=receipts)
    assert len(result) == 1 and len(result[0]['location']['operand_refs']) == 9
    assert len(result[0]['location']['source_refs']) >= 2
    assert len(result[0]['location']['related_findings']) == 2
    assert '支出决算表' in result[0]['message'] and '2013299' in result[0]['message']
    assert all(r['issue_refs'] == [result[0]['id']] for r in receipts)


@pytest.mark.parametrize('fault',['code','stale','unbound','missing_mirror','wrong_parent'])
def test_mirror_grouping_rejects_unrelated_or_unproven_source_identity(fault):
    from src.engine.native_issue_dedup import deduplicate_native_issues
    doc,issues=orphan_pair()
    if fault=='code':issues[1]['location']['orphan_codes']=['3043299']
    elif fault=='stale':issues[1]['location']['fact_context_version_token']='old'
    elif fault=='unbound':issues[1]['location']['operand_refs'][0]['fact_uid']='unknown'
    elif fault=='missing_mirror':issues[1]['location']['classification_mirrors']=[]
    else:issues[1]['location']['classification_mirrors'][0]['parent_code']='20432'
    assert len(deduplicate_native_issues(issues,doc))==2
