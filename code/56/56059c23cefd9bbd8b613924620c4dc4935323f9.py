"""独立复验：同一目录条目的折行不得改变归档冲突结论。"""
import os
import sys
from pathlib import Path
import pytest
sys.path.insert(0, os.environ.get('GBC_REVIEW_SOURCE',str(Path(__file__).parent/'candidate')))
from tests.test_material_writing_rules import archive_document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology,CMM019_CoverElements
from src.engine.rule_outcome import RuleOutcomeSignal

@pytest.mark.parametrize('toc', ['2024年度单位决算情况说明 12','2024年度单位决算情况\n说明 12'], ids=['single_line','wrapped_title'])
def test_toc_title_wrapping_does_not_change_archive_review(toc):
    pages=['上海市测试中心2024年度部门决算',toc,
           '上海市测试中心2024年度部门决算表\n本表反映部门本年度收入。',
           '上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
    doc=archive_document(pages)
    for rule in [CMM018_ScopeTerminology(),CMM019_CoverElements()]:
        try:
            issues=rule.apply(doc)
        except RuleOutcomeSignal as signal:
            issues=signal.partial_issues
        assert any(i.severity=='manual_review' for i in issues), (rule.code,[(i.severity,i.message) for i in issues])
        assert all(i.severity!='warn' for i in issues)
