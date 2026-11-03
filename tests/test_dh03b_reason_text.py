"""DH-03b：未完成原因改为结构化文案，且旧 detail 仍冻结在 legacy 字段中。

验收口径（冻结工具 T8/T10）：
- 义务 detail 与未完成回执 incomplete_reason 非空，且不再出现“缺失或不唯一”；
- legacy_coverage_terms 仍等于旧 detail 算法结果（不随新文案漂移）；
- 原文解释保留，不被清空。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import build_obligation_ledger  # noqa: E402
from src.engine.diagnostic_fields import render_reason_text  # noqa: E402
from src.engine.fact_context import make_fact_receipt  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402


def _profile():
    profile = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年度部门决算.pdf",
        page_texts=["2024年度部门决算"],
    )
    assert profile.kind == "final"
    return profile


def test_old_phrase_removed_and_context_kept():
    entry = {
        "reason_codes": ["operand_missing"],
        "blocked_by": [{"dimension": "cell", "ref": "FIN_03:r2c3", "reason_code": "cell_unbound"}],
        "missing_operands": [{"page": 8, "source": "table", "table_code": "FIN_03"}],
        "candidates": [],
    }
    text = render_reason_text("支出决算表 金额缺失或不唯一，无法复算", entry)
    assert "缺失或不唯一" not in text
    assert "支出决算表" in text and "无法复算" in text
    assert "第8页" in text and "FIN_03" in text


def test_empty_text_still_gets_readable_reason():
    text = render_reason_text("", {"reason_codes": ["rule_no_reason"]})
    assert text.strip()
    assert "规则未给出具体原因" in text


def test_obligation_detail_uses_new_text_and_legacy_fields_stay_old():
    summary = {
        "rule_statuses": {
            "V33-114": "insufficient_data", "V33-109": "insufficient_data",
            "V33-122": "insufficient_data", "CMM-026": "insufficient_data",
        },
        "unresolved_rules": [{
            "rule_id": "V33-109",
            "detail": "“财政拨款收入支出决算总表”事实缺失或不唯一，无法取数",
            "reason_codes": ["operand_missing"],
        }],
    }
    ledger = build_obligation_ledger(_profile(), report_kind="final",
                                     rule_execution_summary=summary)
    item = next(entry for entry in ledger["instances"]
                if entry["obligation_id"] == "OBL-STRUCT-EMPTY-STATEMENT")
    assert "缺失或不唯一" not in item["detail"]
    assert item["detail"].strip()
    # 旧 detail 仍在 legacy 字段中冻结（新文案不参与旧算法）。
    assert item["legacy_coverage_terms"] == ["财政拨款收入支出决算总表"]


def test_receipt_incomplete_reason_rewritten():
    receipt = make_fact_receipt(
        "V33-251", "V33-251:item_sum",
        status="insufficient_data",
        incomplete_reason="分项事实缺失或不唯一，无法加总",
        reason_codes=["operand_missing"],
        missing_operands=[{"page": 21, "source": "table", "table_code": "FIN_03"}],
    )
    text = str(receipt.get("incomplete_reason") or "")
    assert text.strip()
    assert "缺失或不唯一" not in text
    assert "无法加总" in text


def test_passed_receipt_untouched():
    receipt = make_fact_receipt(
        "V33-251", "V33-251:item_sum",
        status="pass",
        incomplete_reason=None,
    )
    assert not receipt.get("incomplete_reason")
