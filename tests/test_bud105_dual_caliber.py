"""MR-4（2026-09-28）BUD-105 对偶口径门槛验收测试。

方案背景：建管委 2026 年度部门预算上，BUD-105 报「T3 与 T5 合计不一致：
T3=328661.81, T5=49376.11 (差额=279285.70)」。实测根因是**口径差**而非
材料错误——差额 279,285.70 万元恰等于该单位《政府性基金预算支出功能
分类预算表》(BUD_T6) 的支出合计：T3 支出总表是全口径，T5 一般公共预算
功能分类表只覆盖一般公共口径，两者本不可直接对比。

修复：BUD-105 在 T3↔T5 比较前做对偶口径门槛——
  (a) T3 合计 == T4 财政拨款收入总计（无事业收入等非财拨来源）；
  (b) BUD_T6 政府性基金支出表不存在或全零。
任一不成立即 RuleDeferred 转人工，不得产出 error（方案 §MR-4）。

夹具为建管委 2026 年度部门预算公开 PDF 的解析产物（公开材料，经
scripts/build_sample_fixture.py 冻结，SHA 双锁：源 PDF + 夹具本体，
本体哈希沿用 WP4-H 的换行归一口径防 autocrlf 漂移）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.engine.budget_rules import BUD105_CrossTableChecks  # noqa: E402
from src.engine.pipeline import build_document, run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import summarize_rule_outcomes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jianguanwei_budget_page_data.json"
#: 源 PDF SHA（uploads/864d06b2…/上海市普陀区建设和管理委员会2026年度部门预算公开.pdf）
SOURCE_SHA = "73528f0b78d178bd83d9b65bcaa77d30269f5e0f67f6ceb6064b819ae479e16a"
#: 夹具本体哈希（换行归一，autocrlf 免疫）
FIXTURE_SHA = "904f132d7d7e21f204173bd5c6c5bcd19ee57e1ec745c4a446011f8593a1978c"
DOC_ID = "SAMPLE-JGW-2026-BUDGET"


def _load_fixture() -> Dict[str, Any]:
    assert FIXTURE.exists(), f"冻结夹具缺失: {FIXTURE}"
    raw = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == FIXTURE_SHA, (
        "夹具内容哈希不符——夹具被修改或需重新生成"
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload.get("doc_id") == DOC_ID
    assert payload.get("source_pdf_sha256") == SOURCE_SHA, "夹具源 PDF SHA 不符"
    return payload


@pytest.fixture(scope="module")
def budget_replay() -> Dict[str, Any]:
    payload = _load_fixture()
    doc = build_document(
        path=f"{DOC_ID}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(
        doc, use_ai_assist=False, report_kind="budget"
    )
    return {
        "issues": issues,
        "summary": summarize_rule_outcomes(outcomes),
    }


def test_bud105_jianguanwei_cascade_zero(budget_replay):
    """级联误报清零：279,285.70 差额不得再以 error 呈现。"""
    bud105 = [
        i for i in budget_replay["issues"] if getattr(i, "rule", "") == "BUD-105"
    ]
    assert not bud105, (
        "BUD-105 仍有 finding: " + "; ".join(str(getattr(i, "message", ""))[:80] for i in bud105)
    )
    for issue in budget_replay["issues"]:
        assert "279285.70" not in str(getattr(issue, "message", "")), (
            "级联差额仍出现在其它 finding 中"
        )


def test_bud105_deferred_with_caliber_reason(budget_replay):
    """门槛路径：BUD-105 六态为 insufficient_data，原因精确指向口径根因。"""
    unresolved = {
        item.get("rule_id"): item
        for item in budget_replay["summary"]["unresolved_rules"]
    }
    assert "BUD-105" in unresolved
    item = unresolved["BUD-105"]
    assert item["status"] == "insufficient_data"
    reasons = "；".join(str(r) for r in (item.get("unresolved_reasons") or []))
    assert "对偶口径不可比" in reasons
    assert "BUD_T6 非零" in reasons


def test_bud105_other_check_pairs_still_run(budget_replay):
    """门槛不得顺带关闭其它检查对：T1↔T4 检查面仍在（其 unresolved 理由
    来自既有的 T4 支出总计严格提取局限，与本门槛无关）。"""
    unresolved = {
        item.get("rule_id"): item
        for item in budget_replay["summary"]["unresolved_rules"]
    }
    reasons = "；".join(
        str(r) for r in (unresolved.get("BUD-105", {}).get("unresolved_reasons") or [])
    )
    assert "T1与T4支出总计数值缺失" in reasons


def test_fixture_source_sha_pinned():
    _load_fixture()  # SHA 双锁断言在加载器内
