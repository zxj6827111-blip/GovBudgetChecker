"""金额范围是否仍需填写只看实际未完成的检查，写法确定不代表金额检查已完成。"""
from src.services.document_scope import amount_dependent_rules


def test_only_rules_actually_waiting_for_amount_scope_are_listed():
    summary = {"unresolved_rules": [
        {"rule_id": "BUD-110", "detail": "依赖材料汇总口径的检查未完成"},
        {"rule_id": "V33-236", "detail": "材料口径待确认"},
        {"rule_id": "CMM-019", "detail": "封面文种缺少独立依据"},
        {"rule_id": "BUD-110", "reason_codes": ["org_scope_pending"]},
        {"rule_id": "V33-240", "detail": "表内勾稽未取得数值"},
    ]}
    assert amount_dependent_rules(summary) == ["BUD-110", "V33-236"]
    assert amount_dependent_rules(None) == []
    assert amount_dependent_rules({"unresolved_rules": [{"rule_id": "CMM-018", "detail": "写法口径未确认"}]}) == []
