"""接续基线反例：其他页、表实例的发现不能完成当前金额义务。"""
import os
import sys

sys.path.insert(0, os.environ["GBC_REVIEW_CODE_ROOT"])

from src.schemas.material_detail import CoverageObligationItem
from src.services.obligation_finding_coverage import obligation_covered_by_finding


def test_other_page_and_table_amount_finding_cannot_close_obligation():
    obligation = CoverageObligationItem(
        obligation_id="OBL-TXT-T5", group_id="TABLE_TEXT",
        title="功能分类金额核对", status="insufficient_data",
        reason="insufficient_data", blocks_gate=True,
        detail="第8页说明中的“住房公积金”决算金额无法绑定",
        checkers=["V33-222"], legacy_coverage_terms=["住房公积金"],
        missing_operands=[{
            "page": 8, "table_instance_id": "T5-current",
            "column": "决算数", "role": "narrative_amount", "row_key": "2210201",
        }],
    )
    result = {"rule_findings": [{
        "id": "other-location", "rule_id": "V33-222", "tags": ["V33-222"],
        "severity": "warn", "source": "rule",
        "message": "住房公积金金额不一致：说明100万元，表内90万元，相差10万元。",
        "location": {"page": 2, "table_instance_id": "T5-other",
                     "row": "2210201", "check_relation_id": "other-amount"},
    }]}
    assert not obligation_covered_by_finding(
        obligation, result, visible_issue_ids={"other-location"}
    ), "同名科目不等于同一检查关系；其他页/表发现不得关闭当前义务"
