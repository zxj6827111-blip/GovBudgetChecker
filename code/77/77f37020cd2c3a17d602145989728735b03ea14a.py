"""只拆开既有根因验证；临时副本，不启动业务、不修改交付。"""
import importlib.util
from pathlib import Path

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
spec = importlib.util.spec_from_file_location('submitted_v2_helpers', ROOT / 'outputs/real_painpoint_effect_tool_closeout_20261105/test_effect_acceptance_review_v2.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_unknown_operand_alone_is_rejected(tmp_path):
    candidate = helper.clone_collection(helper.CANDIDATE, tmp_path / 'candidate')
    key = ('OBL-SG-TOTAL', 'V33-244', 'three_public:决算:total_actual:sum')
    def change(obj):
        r = helper.receipt(obj, key)
        assert r['formula'] == 'total = sum(parts) (万元)'
        # 仅替换操作数；保留合法公式和原残差，避免靠另一项错误触发拒绝。
        r['operand_fact_ids'] = ['review_fact_that_does_not_exist']
    helper.alter(candidate, 'records', 'RA-031', change)
    assert helper.outcome(candidate) == 'FAIL', '回执把自己声明的伪造事实加入事实宇宙后通过'


def test_unrelated_sample_cannot_borrow_removal_authorization(tmp_path):
    candidate = helper.clone_collection(helper.CANDIDATE, tmp_path / 'candidate')
    def change(obj):
        issues = obj['review_report']['issues']
        removed = [i for i in issues if i.get('rule_id') == 'CMM-007']
        assert len(removed) == 1
        assert removed[0]['page_number'] == 19
        # RA-001 的这条问题无本材料撤销依据，不得借 RA-031 的同规则迁移放行。
        obj['review_report']['issues'] = [i for i in issues if i.get('rule_id') != 'CMM-007']
    helper.alter(candidate, 'status', 'RA-001', change)
    assert helper.outcome(candidate) == 'FAIL', '另一材料同规则的恢复错误授权了本材料问题消失'


def test_known_issue_cannot_lose_evidence_as_only_info(tmp_path):
    candidate = helper.clone_collection(helper.CANDIDATE, tmp_path / 'candidate')
    def change(obj):
        issue = next(i for i in obj['review_report']['issues'] if i.get('rule_id') == 'CMM-007')
        assert issue.get('evidence')
        issue['evidence'] = []
        issue['location'] = None
        issue['bbox'] = None
    helper.alter(candidate, 'status', 'RA-001', change)
    assert helper.outcome(candidate) == 'FAIL', '既有正式问题证据被删仅记 INFO，整体仍 PASS'
