"""R2.2 收尾：card_summary / suggested_actions 全链承接（计划 §7 全链贯通）。

R1/QP-07 与第六轮验收边界指出：只给引擎账本加字段不够，
material_detail_query_service 与响应 Pydantic 模型必须显式承接，
且要有真实展示/导出链。本文件逐层验证：

1. 引擎：由已登记结构化缺数派生摘要与建议动作；完成态不编造（None/空）；
   AI 义务给固定路由文案。
2. 序列化往返：to_dict → 落库形状（dict）→ build_coverage → Pydantic 模型。
3. 旧结果兼容：缺键的 instances 读回为 None/空列表，不二次推断。
4. 导出：unfinished_checks 携带两个字段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_AI_NOT_RUN,
    OBLIGATION_INSUFFICIENT_DATA,
    _NEXT_ACTION_LABELS,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.schemas.document_profile import DocumentProfile  # noqa: E402
from src.schemas.material_detail import CoverageObligationItem  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402
from src.services.material_detail_query_service import build_coverage  # noqa: E402


def _profile() -> DocumentProfile:
    profile = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年度部门决算.pdf",
        page_texts=["2024年度部门决算"],
    )
    assert profile.kind == "final"
    return profile


def _receipt(statuses: dict[str, str] | None = None) -> dict:
    return {"rule_statuses": dict(statuses or {})}


def _instance(ledger: dict, obligation_id: str) -> dict:
    return next(item for item in ledger["instances"] if item["obligation_id"] == obligation_id)


def _insufficient_ledger() -> dict:
    """V33-121 取数不足并登记真实缺数位置的账本。"""
    return build_obligation_ledger(
        _profile(),
        report_kind="final",
        rule_execution_summary=_receipt({
            "V33-121": "insufficient_data",
            "V33-244": "pass",
        }),
        fact_receipts=[
            {
                "rule_id": "V33-121",
                "sub_check_id": "V33-121:r1",
                "status": "insufficient_data",
                "reason_codes": ["operand_missing"],
                "missing_operands": [
                    {
                        "source": "table",
                        "table_code": "FIN_04_fiscal_grant_total",
                        "role": "total_actual/决算操作数",
                        "page": 19,
                        "row_key": "total",
                        "column": "total_actual",
                    }
                ],
            }
        ],
    )


def test_unresolved_instance_derives_summary_and_actions_from_registered_operands():
    ledger = _insufficient_ledger()
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["status"] == OBLIGATION_INSUFFICIENT_DATA
    # 摘要只引用已登记的表代码/角色/页码，不新造表名或页码
    assert total["card_summary"] == (
        "取数不足：缺 「FIN_04_fiscal_grant_total」的total_actual/决算操作数（第19页）"
    )
    actions = total["suggested_actions"]
    # 首条来自 DH-02 next_action 路由，其后逐缺数位置
    assert actions[0]["action"] == _NEXT_ACTION_LABELS[total["next_action"]]
    operand_actions = [item for item in actions if item["action"].startswith("核对")]
    assert operand_actions == [
        {
            "action": "核对「FIN_04_fiscal_grant_total」的total_actual/决算操作数（第19页）",
            "page": 19,
        }
    ]


def test_operand_without_page_never_invents_one():
    ledger = build_obligation_ledger(
        _profile(),
        report_kind="final",
        rule_execution_summary=_receipt({"V33-121": "insufficient_data", "V33-244": "pass"}),
        fact_receipts=[
            {
                "rule_id": "V33-121",
                "status": "insufficient_data",
                "reason_codes": ["operand_missing"],
                "missing_operands": [
                    {"source": "table", "table_code": "FIN_07_three_public", "role": "合计", "page": None},
                ],
            }
        ],
    )
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["card_summary"] == "取数不足：缺 「FIN_07_three_public」的合计"
    assert all("page" not in item for item in total["suggested_actions"] if item["action"].startswith("核对"))


def test_completed_and_not_applicable_do_not_fabricate_summary():
    ledger = build_obligation_ledger(
        _profile(),
        report_kind="final",
        rule_execution_summary=_receipt({code: "pass" for code in registered_rule_ids("final")}),
    )
    for item in ledger["instances"]:
        if item["status"] == "completed":
            assert item["card_summary"] is None
            assert item["suggested_actions"] == []


def test_ai_instance_carries_fixed_routing_summary():
    ledger = build_obligation_ledger(
        _profile(),
        report_kind="final",
        rule_execution_summary=_receipt({code: "pass" for code in registered_rule_ids("final")}),
        ai_execution={"state": "not_run"},
        ai_required=True,
    )
    ai = _instance(ledger, "OBL-AI-SEMANTIC-REVIEW")
    assert ai["status"] == OBLIGATION_AI_NOT_RUN
    assert ai["card_summary"] == "语义模型未执行"
    assert ai["suggested_actions"] == [{"action": "重跑语义复核"}]


def test_serialization_roundtrip_reaches_pydantic_model():
    """to_dict → 落库 dict → build_coverage → Pydantic 显式承接。"""
    ledger = _insufficient_ledger()
    payload = json.loads(json.dumps(ledger, ensure_ascii=False))
    block = build_coverage(payload)
    assert block.available is True
    item = next(
        raw
        for raw in block.items
        if raw.obligation_id == "OBL-SG-TOTAL"
    )
    assert item.card_summary is not None and "FIN_04_fiscal_grant_total" in item.card_summary
    operand_actions = [entry for entry in item.suggested_actions if entry["action"].startswith("核对")]
    assert operand_actions and operand_actions[0]["page"] == 19


def test_legacy_instances_without_new_keys_stay_none_and_empty():
    """旧结果缺键：读回 None/空列表，展示层回退 detail/状态，不二次推断。"""
    block = build_coverage(
        {
            "applicable_total": 1,
            "completed_total": 0,
            "not_applicable_total": 0,
            "unresolved_total": 1,
            "blocking_total": 1,
            "instances": [
                {
                    "obligation_id": "OBL-OLD-1",
                    "status": "insufficient_data",
                    "reason": "insufficient_data",
                }
            ],
        }
    )
    item = CoverageObligationItem.model_validate(block.items[0])
    assert item.card_summary is None
    assert item.suggested_actions == []


def test_export_unfinished_checks_carry_the_two_fields():
    from api.routes.reports import _build_check_scope_export

    ledger = _insufficient_ledger()
    scope = _build_check_scope_export({"result": {"meta": {"obligation_coverage": ledger}}})
    unfinished = scope["unfinished_checks"]
    target = next(item for item in unfinished if item["obligation_id"] == "OBL-SG-TOTAL")
    assert target["card_summary"] is not None
    assert target["suggested_actions"]


def test_missing_operand_without_real_anchor_yields_no_phrase():
    """无真实表/章节锚的缺数不成句：摘要回退原因标签，动作不编造。"""
    from src.engine.check_obligations import ObligationInstance

    instance = ObligationInstance(
        obligation_id="OBL-X",
        group_id="TABLE_INTERNAL",
        group_title="表内关系",
        title="x",
        status=OBLIGATION_INSUFFICIENT_DATA,
        reason=OBLIGATION_INSUFFICIENT_DATA,
        missing_operands=({"source": "unknown", "role": " mystery "}, {"source": "table"}),
    )
    assert instance.card_summary is None
    assert instance.suggested_actions == ()
