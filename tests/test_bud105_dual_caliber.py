"""MR-4（2026-09-28）BUD-105 对偶口径门槛验收测试。

方案背景：建管委 2026 年度部门预算上，BUD-105 报「T3 与 T5 合计不一致：
T3=328661.81, T5=49376.11 (差额=279285.70)」。实测根因是**口径差**而非
材料错误——差额 279,285.70 万元恰等于该单位《政府性基金预算支出功能
分类预算表》(BUD_T6) 的支出合计：T3 支出总表是全口径，T5 一般公共预算
功能分类表只覆盖一般公共口径，两者本不可直接对比。

修复：BUD-105 在 T3↔T5 比较前做对偶口径门槛——
  (a) T3 合计 == T4 财政拨款收入总计（无事业收入等非财拨来源）；
  (b) BUD_T6 政府性基金支出表不存在或全零。
任一不成立即识别不出「同一口径的两个总计」，T3↔T5 差额是口径差。

B 档一期（2026-09-29，MR-B2）演进：门槛从「T6 非零即转人工」升级为
「差额=T6 已实证才放行」——T3−T5 恰等于 T6 政府性基金支出合计（动态
包络内）时口径差获正面证据，两表相互印证，直接放行（pass、无 finding）；
差额对不上 T6 或 T6 金额取不到 → 保持 RuleDeferred（见
test_bud105_gate_keeps_deferred_when_diff_not_t6 变异锁）。同批修好
T4 五列横表严格取数（表头「合计」列定位），T1↔T4 检查对真实可核。
三份预算材料的 T3/T5/T8 若整体无「合计/总计」行（DOC-B1-006 财务收支
预算模板实测），检查对结构性不适用，与取数失败严格区分。

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
def budget_payload() -> Dict[str, Any]:
    """夹具原始 payload（不重放）——供保护路径的变异测试改动表格后重建文档。"""
    return _load_fixture()


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


def test_bud105_resolved_after_verified_caliber_and_t4_fix(budget_replay):
    """B 档一期（2026-09-29）演进后的锁定行为：

    - MR-B2 修好 T4 五列横表取数后，T1↔T4 支出总计可核且一致；
    - T3↔T5 差额 279,285.70 == T6 政府性基金支出合计（含项目字段），
      口径差获**正面实证** → 按红线「有正面口径差证据才降级」直接放行；
    - BUD-105 六态为 pass（无 finding、无 unresolved）——口径一致本就
      无事可报，静默通过；差额=T6 的验证路径由
      test_bud105_gate_keeps_deferred_when_diff_not_t6 变异锁定。
    """
    summary = budget_replay["summary"]
    assert summary["rule_statuses"].get("BUD-105") == "pass"
    unresolved = {
        item.get("rule_id"): item
        for item in summary["unresolved_rules"]
    }
    assert "BUD-105" not in unresolved


def test_bud105_gate_keeps_deferred_when_diff_not_t6(budget_payload):
    """保护路径变异锁：把 T6 合计金额改小使 T3−T5 ≠ T6 → 口径差失去
    正面实证 → BUD-105 必须 Deferred（不得静默吞掉真差异）。"""
    payload = json.loads(json.dumps(budget_payload))
    replaced = 0
    for table in payload["page_tables"]:
        if not isinstance(table, list):
            continue
        for row in table:
            if not isinstance(row, list):
                continue
            for ci, cell in enumerate(row):
                if cell and "2,792,857,000" in str(cell):
                    row[ci] = "2,000,000,000.00"
                    replaced += 1
    assert replaced >= 1, "夹具中未找到 T6 政府性基金合计单元格"

    doc = build_document(
        path=f"{DOC_ID}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False, report_kind="budget")
    summary = summarize_rule_outcomes(outcomes)
    unresolved = {
        item.get("rule_id"): item
        for item in summary["unresolved_rules"]
    }
    assert "BUD-105" in unresolved, "差额≠T6 时 BUD-105 必须 Deferred 转人工"
    reasons = "；".join(str(r) for r in (unresolved["BUD-105"].get("unresolved_reasons") or []))
    assert "对偶口径不可比" in reasons
    assert "未获 T6 政府性基金支出金额实证" in reasons
    # 差额不得以 error finding 呈现
    errors = [
        i for i in issues
        if getattr(i, "rule", "") == "BUD-105"
        and str(getattr(i, "severity", "")).lower() == "error"
    ]
    assert not errors


def test_bud105_t1_t4_pair_resolves_after_t4_fix(budget_replay):
    """MR-B2 修好 T4 五列横表后，T1↔T4 支出总计检查对必须真实可核
    （旧锁「T1与T4支出总计数值缺失」随取数修复废止）。"""
    statuses = budget_replay["summary"]["rule_statuses"]
    assert statuses.get("BUD-105") == "pass"
    # BUD-107 同样吃到 T4 修复（T4↔文本可核）且不再产出截断误报
    bud107 = [
        i for i in budget_replay["issues"] if getattr(i, "rule", "") == "BUD-107"
    ]
    assert not bud107, "BUD-107 仍有 finding: " + "; ".join(
        str(getattr(i, "message", ""))[:80] for i in bud107
    )


def test_fixture_source_sha_pinned():
    _load_fixture()  # SHA 双锁断言在加载器内
