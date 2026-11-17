"""D2a 基金路径统一身份绑定：款名差异不再阻断金额核对。

背景（2026-11-12 民政批次诊断·养老服务发展中心）：说明写
「其他支出（类）彩票公益金安排支出（款）用于社会福利的彩票公益金支出（项）」
（款名少"的"），表内款名为「彩票公益金安排的支出」。旧实现在款名逐字不一致时
整条金额关系阻断；类名+项名逐字相等且该类下同名项唯一时，(类,项) 已唯一确定
表行，金额关系应照常核对，款名差异单独报告（不吞、不抵销）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.pipeline import build_document  # noqa: E402
from src.engine.rule_outcome import RuleDeferred  # noqa: E402
from src.engine.rules_v33 import R33TxtFundDetail, _txt_fund_match_item  # noqa: E402

TRUTH_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
TRUTH_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
TRUTH_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"
PAGE_FUND_NARRATIVE = 37

#: 养老服务发展中心 2025 单位决算（job 36c06139…）FIN_08 的真实层级与金额：
#: 229 其他支出 / 22960 彩票公益金安排的支出 / 2296002 用于社会福利的
#: 彩票公益金支出 462.95 万元。说明侧款名漏"的"（诊断冻结事实）。
REAL_ITEMS = [
    {"code": "229", "name": "其他支出", "amount": None, "page": 16},
    {"code": "22960", "name": "彩票公益金安排的支出", "amount": None, "page": 16},
    {"code": "2296002", "name": "用于社会福利的彩票公益金支出", "amount": "462.95", "page": 16},
]
REAL_HIERARCHY = {item["code"]: item["name"] for item in REAL_ITEMS}


def test_kuan_name_difference_binds_via_unique_klass_xiang():
    item, reason = _txt_fund_match_item(
        REAL_ITEMS, REAL_HIERARCHY, "其他支出", "彩票公益金安排支出", "用于社会福利的彩票公益金支出")
    assert item is not None and reason == ""
    assert item["code"] == "2296002" and str(item["amount"]) == "462.95"
    basis = item["identity_basis"]
    assert basis["kind"] == "klass_xiang_unique"
    assert basis["unconfirmed_level"] == "kuan"
    assert basis["narrative_kuan"] == "彩票公益金安排支出"
    assert basis["table_kuan"] == "彩票公益金安排的支出"
    assert basis["kuan_code"] == "22960"


def test_exact_three_level_match_keeps_no_basis():
    item, reason = _txt_fund_match_item(
        REAL_ITEMS, REAL_HIERARCHY, "其他支出", "彩票公益金安排的支出", "用于社会福利的彩票公益金支出")
    assert item is not None and "identity_basis" not in item


def test_ambiguous_same_xiang_under_two_kuan_fails_closed():
    items = REAL_ITEMS + [
        {"code": "22961", "name": "另一款（示例）", "amount": None, "page": 16},
        {"code": "2296101", "name": "用于社会福利的彩票公益金支出", "amount": "1.00", "page": 16},
    ]
    hierarchy = {item["code"]: item["name"] for item in items}
    item, reason = _txt_fund_match_item(
        items, hierarchy, "其他支出", "彩票公益金安排支出", "用于社会福利的彩票公益金支出")
    assert item is None, "同名项在同类两款下不唯一，不得绑定"


def test_missing_table_kuan_name_fails_closed():
    hierarchy = {code: name for code, name in REAL_HIERARCHY.items() if code != "22960"}
    item, _ = _txt_fund_match_item(
        REAL_ITEMS, hierarchy, "其他支出", "彩票公益金安排支出", "用于社会福利的彩票公益金支出")
    assert item is None, "表内款行名称缺失时无从证明差异来源，不得进入兜底"


def test_wrong_xiang_or_klass_never_binds():
    assert _txt_fund_match_item(
        REAL_ITEMS, REAL_HIERARCHY, "其他支出", "彩票公益金安排支出", "完全不同的项")[0] is None
    assert _txt_fund_match_item(
        REAL_ITEMS, REAL_HIERARCHY, "社会保障和就业支出", "彩票公益金安排支出",
        "用于社会福利的彩票公益金支出")[0] is None


def _truth_payload() -> Dict[str, Any]:
    assert hashlib.sha256(TRUTH_FIXTURE.read_bytes()).hexdigest() == TRUTH_FIXTURE_SHA
    payload = json.loads(TRUTH_FIXTURE.read_text(encoding="utf-8"))
    assert payload["source_pdf_sha256"] == TRUTH_SOURCE_SHA
    return payload


def test_y03_kuan_typo_still_executes_amount_and_reports_name_difference():
    """宜川真值变异：说明款名漏一字（复现养老真实形态）。

    期望两件事同时成立：
    1. Y03 金额矛盾（表 105.00 vs 说明 135.00）继续报告——名称问题不抵销金额核对；
    2. 新增一条款名差异 warn 发现，携带两侧原文名称——金额核对不吞名称差异。
    """
    data = _truth_payload()
    page = data["page_texts"][PAGE_FUND_NARRATIVE - 1]
    old = "超长期特别国债安排的支出（款）"
    assert old in page, "宜川 P37 款名原文变化，请核对夹具"
    data["page_texts"][PAGE_FUND_NARRATIVE - 1] = page.replace(old, "超长期特别国债安排支出（款）")
    doc = build_document(path="y03_kuan_typo.pdf",
                         page_texts=list(data["page_texts"]),
                         page_tables=json.loads(json.dumps(data["page_tables"])),
                         filesize=0)
    issues = list(R33TxtFundDetail().apply(doc))
    name_issues = [i for i in issues if i.location.get("name_kind") == "kuan_name_difference"]
    amount_issues = [i for i in issues if i not in name_issues]
    assert len(amount_issues) == 1, [i.message for i in issues]
    assert amount_issues[0].location["table_amount"] == "105.00"
    assert amount_issues[0].location["narrative_amount"] == "135.00"
    assert len(name_issues) == 1, [i.message for i in issues]
    assert name_issues[0].severity == "warn"
    shown = name_issues[0].message
    assert "超长期特别国债安排的支出" in shown and "超长期特别国债安排支出" in shown
    assert name_issues[0].location["fund_scope"] == "政府性基金"
    assert name_issues[0].location["row"].startswith("2219899")
