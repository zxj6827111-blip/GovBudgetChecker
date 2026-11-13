"""独立复验：页脚页码不得让合格正文表注变成目录证据。"""
import os
import sys
from pathlib import Path
import pytest
sys.path.insert(0, os.environ.get('GBC_REVIEW_SOURCE', str(Path(__file__).parent/'candidate')))
from tests.test_material_writing_rules import archive_document
from src.engine.checklist_identity_rules import CMM018_ScopeTerminology, CMM019_CoverElements
from src.engine.rule_outcome import RuleOutcomeSignal

@pytest.mark.parametrize('footer', ['', '\n12'], ids=['no_footer', 'separate_page_number'])
def test_native_table_note_is_not_removed_by_separate_page_number(footer):
    pages=['上海市测试中心2024年度部门决算',
           '上海市测试中心2024年度部门决算表\n本表反映部门本年度收入情况'+footer,
           '上海市测试中心2024年度部门决算情况说明\n本部门经费情况见相关说明。']
    doc=archive_document(pages)
    for rule in [CMM018_ScopeTerminology(),CMM019_CoverElements()]:
        try:
            issues=rule.apply(doc)
        except RuleOutcomeSignal as signal:
            issues=signal.partial_issues
        assert any(i.severity=='manual_review' for i in issues), (rule.code, [(i.severity,i.message) for i in issues])
        assert all(i.severity!='warn' for i in issues)
