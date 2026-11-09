"""合并前的成员编号不得与另一条合并后报告问题串号。"""
from copy import deepcopy

from src.engine import pipeline
from src.engine.native_issue_dedup import deduplicate_native_issues, link_merged_issue_receipts
from src.services.review_problem_set import collect_review_problems, problem_status
from tests.remediation.test_blank_parent_dedup import case


def test_grouping_and_sorting_never_reassign_member_id_to_another_current_problem():
    doc, first, duplicate = case()
    # 一个已并组公式，加一条同规则的独立问题。
    other = deepcopy(first)
    other.message = '另一条尚未处理的独立检查'
    other.location = {'page':1,'column':'project_actual'}
    other.check_relation_id = 'different-relation'
    items = deduplicate_native_issues([first, duplicate, other], doc)
    assert len(items) == 2
    # 特意逆序输出，复现“按最终序号赋 id”与原成员 id 的重号。
    exported = [pipeline._issue_to_dict(item, idx, doc) for idx,item in enumerate(reversed(items),1)]
    ids = {item['id'] for item in exported}
    assert len(ids) == 2
    for item in exported:
        aliases = set(item.get('merged_issue_ids') or [])
        assert not aliases.intersection(ids - {item['id']})
    problems = collect_review_problems(job_uuid='job',rule_findings=exported)
    chosen = problems[0]
    other_problem = problems[1]
    assert problem_status(other_problem,{chosen.issue_id:'confirmed'}) == 'pending'
    assert problem_status(other_problem,{chosen.issue_id:'no_issue'}) == 'pending'


def test_merged_failure_receipts_point_to_the_group_owning_the_original_member():
    doc, first, duplicate = case()
    grouped = deduplicate_native_issues([first,duplicate],doc)
    exported = [pipeline._issue_to_dict(grouped[0],9,doc)]
    aliases = exported[0]['merged_issue_ids']
    receipts = [{'status':'fail','rule_id':'legacy','sub_check_id':'one','issue_refs':[alias]} for alias in aliases]
    link_merged_issue_receipts(exported,receipts)
    assert all(receipt['issue_refs'] == [exported[0]['id']] for receipt in receipts)
