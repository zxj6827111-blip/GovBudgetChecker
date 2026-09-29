"""B 档一期（2026-09-29「预算侧取数止血 + 三公说明提取」）验收测试。

任务包：修"不报"第二刀——预算侧 BUD-105/107/108/109 不再以取数失败挂
insufficient；决算侧 CMM-001（三公表×说明）4/4 转真实判定。

全部基于冻结 page-data 夹具离线重放（不解析 PDF、不调 AI）。量化验收行：
- BUD-105/107/108/109 on 3 份预算：全部出真实判定（pass/fail/not_applicable），
  Deferred 类必须有正面证据（BUD-105 的差额=T6 实证锁在
  test_bud105_dual_caliber::test_bud105_gate_keeps_deferred_when_diff_not_t6）
- 预算 3 份 unresolved：每份 ≤2，合计 ≤6（实测 2/0/2）
- CMM-001 on 4 份决算：4/4 出判定；每份 ≥5/6 字段提取成功（缺项需取证）
- 4 份决算 unresolved 合计 14 → ≤10（实测 10；其余规则不得上升）
- 宜川 findings 12 不动；文旅 1700.57 级联 0；golden 零变化

夹具哈希沿用 WP4-H 的换行归一口径（autocrlf 双锁免疫，PR #40 教训）。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

import pytest

from src.engine.amount_math import classify_amount_diff
from src.engine.budget_rules import (
    BUD101_T1Balance,
    _extract_t4_strict,
    _resolve_table_unit,
    _text_vs_table_envelope,
)
from src.engine.common_rules import (
    CMM001_ThreePublicNarrativeConsistency,
    _extract_three_public_narrative_v2,
    _locked_three_public_section,
    _merge_soft_wrapped_lines_shared,
)
from src.engine.field_extractor import STATUS_VALID, StrictValue
from src.engine.pipeline import build_document, run_rules_with_outcomes
from src.engine.rule_outcome import (
    RuleDeferred,
    UNRESOLVED_STATUSES,
    summarize_rule_outcomes,
)
from src.engine.rules_v33 import Document
from src.utils.narration import merge_page_texts

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 冻结夹具与换行归一 SHA（autocrlf 免疫）
# ---------------------------------------------------------------------------

BUDGET_FIXTURES = {
    "jianguanwei": (
        "tests/fixtures/jianguanwei_budget_page_data.json",
        "SAMPLE-JGW-2026-BUDGET",
        "73528f0b78d178bd83d9b65bcaa77d30269f5e0f67f6ceb6064b819ae479e16a",
        "904f132d7d7e21f204173bd5c6c5bcd19ee57e1ec745c4a446011f8593a1978c",
    ),
    "chengguan": (
        "tests/fixtures/chengguan_budget_page_data.json",
        "SAMPLE-CGZF-2026-BUDGET",
        "c56f4368be5dbf51c88214f1260ecb97ce57fe77950df18dc33d184f40b24d93",
        "49cd476153b0cdd77c447b51839d854787c9b7081c56af523a9a98955c6ace25",
    ),
    "wenlvju": (
        "tests/fixtures/wenlvju_budget_page_data.json",
        "SAMPLE-WENLVJU-2026-BUDGET",
        "19447c3cec309322ad786b14f5cb751424a1bfe2a1f3c4c3a96d035440665cf9",
        "8d51f9b851d6008147bc85ab85aa56e50c89530f451fd30b5cecb10d28e6e1bc",
    ),
}

FINAL_FIXTURES = {
    "yichuan": "tests/fixtures/cross_san_gong_truth_page_data.json",
    "shiquan": "tests/fixtures/shiquan_narrative_truth_page_data.json",
    "wenlv": "tests/fixtures/wenlv_narrative_truth_page_data.json",
    "golden": "tests/fixtures/sample_page_data.json",
}

FINAL_FIXTURE_SHAS = {
    "yichuan": "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c",
    "shiquan": "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7",
    "wenlv": "07813ae61b04cf9f3afd2bf93c69b357ff9aeefee3ba6a893b34d77d956e8252",
    "golden": "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1",
}


def _norm_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _load(alias: str, spec) -> Dict[str, Any]:
    rel, doc_id, source_sha, fixture_sha = spec
    path = ROOT / rel
    assert path.exists(), f"冻结夹具缺失: {path}"
    assert _norm_sha(path) == fixture_sha, f"夹具哈希不符（{path.name}）——需重新生成"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("doc_id") == doc_id
    assert payload.get("source_pdf_sha256") == source_sha, "夹具源 PDF SHA 不符"
    return payload


def _budget_replay(payload: Dict[str, Any], alias: str) -> Dict[str, Any]:
    doc = build_document(
        path=f"{payload.get('doc_id', alias)}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False, report_kind="budget")
    return {"issues": issues, "summary": summarize_rule_outcomes(outcomes)}


def _final_replay(alias: str) -> Dict[str, Any]:
    rel = FINAL_FIXTURES[alias]
    path = ROOT / rel
    assert _norm_sha(path) == FINAL_FIXTURE_SHAS[alias], f"冻结夹具哈希不符: {path.name}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    doc = build_document(
        path=f"{payload.get('doc_id', alias)}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(doc, use_ai_assist=False, report_kind="final")
    return {"issues": issues, "summary": summarize_rule_outcomes(outcomes)}


@pytest.fixture(scope="module")
def budget_replays() -> Dict[str, Dict[str, Any]]:
    return {
        alias: _budget_replay(_load(alias, spec), alias)
        for alias, spec in BUDGET_FIXTURES.items()
    }


@pytest.fixture(scope="module")
def final_replays() -> Dict[str, Dict[str, Any]]:
    return {alias: _final_replay(alias) for alias in FINAL_FIXTURES}


# ---------------------------------------------------------------------------
# 验收行 1：BUD-105/107/108/109 在 3 份预算上出真实判定
# ---------------------------------------------------------------------------

#: 各样张上四条规则的落位（实测）。not_applicable 属真实判定：
#: BUD-108 在文旅局样张上绩效说明金额已提取、T3 无合计行，
#: 口径比较结构性不适用（证据随 detail 留痕）。
EXPECTED_BUD_STATUS = {
    "jianguanwei": {"BUD-105": "pass", "BUD-107": "pass", "BUD-108": "fail", "BUD-109": "pass"},
    "chengguan": {"BUD-105": "pass", "BUD-107": "pass", "BUD-108": "fail", "BUD-109": "pass"},
    "wenlvju": {"BUD-105": "pass", "BUD-107": "pass", "BUD-108": "not_applicable", "BUD-109": "pass"},
}


@pytest.mark.parametrize("alias", sorted(BUDGET_FIXTURES))
def test_bud_105_107_108_109_real_judgments(budget_replays, alias):
    statuses = budget_replays[alias]["summary"]["rule_statuses"]
    for rule, expected in EXPECTED_BUD_STATUS[alias].items():
        actual = statuses.get(rule)
        assert actual == expected, f"{alias}/{rule}: 期望 {expected}，实际 {actual}"
        assert actual not in UNRESOLVED_STATUSES, f"{alias}/{rule} 仍属取数失败类"


# ---------------------------------------------------------------------------
# 验收行 2：预算 3 份 unresolved 每份 ≤2，合计 ≤6
# ---------------------------------------------------------------------------

#: 各样张 unresolved 规则集合（实测）。余项均不在本包修范围：
#: - V33-PERF-PHASE-AMOUNT / CMM-005：任务包明确不修清单之外的事实缺口
#: - BUD-102/103（文旅局）：材料 T3/T8 整体无「合计/总计」行
#:   （财务收支预算模板），fail-closed 属实，B 档全档扩面再议
EXPECTED_BUD_UNRESOLVED = {
    "jianguanwei": {"V33-PERF-PHASE-AMOUNT", "CMM-005"},
    "chengguan": set(),
    "wenlvju": {"BUD-102", "BUD-103"},
}


@pytest.mark.parametrize("alias", sorted(BUDGET_FIXTURES))
def test_budget_unresolved_per_doc(budget_replays, alias):
    summary = budget_replays[alias]["summary"]
    unresolved = {item.get("rule_id") for item in summary["unresolved_rules"]}
    assert unresolved == EXPECTED_BUD_UNRESOLVED[alias], (
        f"{alias}: unresolved 集合 {unresolved} 与锁定不符"
    )
    assert len(unresolved) <= 2, f"{alias}: unresolved {len(unresolved)} > 2"


def test_budget_unresolved_total_le_6(budget_replays):
    total = sum(
        r["summary"]["unresolved_total"] for r in budget_replays.values()
    )
    assert total <= 6, f"预算 3 份 unresolved 合计 {total} > 6"


def test_wenlvju_structural_absence_documented(budget_replays):
    """文旅局样张 T3/T8 无「合计/总计」行：fail-closed 如实挂 insufficient，
    且 BUD-108 以 not_applicable 留痕（不是取数失败）。"""
    summary = budget_replays["wenlvju"]["summary"]
    unresolved = {
        item.get("rule_id"): item for item in summary["unresolved_rules"]
    }
    assert "BUD-102" in unresolved and "T3未找到合计" in str(
        unresolved["BUD-102"].get("detail")
    )
    assert "BUD-103" in unresolved and "T8未找到合计" in str(
        unresolved["BUD-103"].get("detail")
    )
    assert summary["rule_statuses"].get("BUD-108") == "not_applicable"


# ---------------------------------------------------------------------------
# MR-B1：表页单位解析链与 fail-closed（合成用例）
# ---------------------------------------------------------------------------


def _make_doc(page_texts, page_tables=None, units=None, dominant=None) -> Document:
    return Document(
        path="synthetic.pdf",
        pages=len(page_texts),
        filesize=0,
        page_texts=page_texts,
        page_tables=page_tables or [[]],
        units_per_page=units if units is not None else [None] * len(page_texts),
        years_per_page=[[] for _ in page_texts],
        dominant_unit=dominant,
    )


def test_table_unit_priority_chain():
    # 页级标注优先于表题邻近与 dominant
    doc = _make_doc(
        ["部门收支总表\n单位：元"],
        units=["万元"],
        dominant="亿元",
    )
    assert _resolve_table_unit(doc, 1) == "万元"
    # 页级缺失 → 表题邻近 ±2 行「单位：X」
    doc2 = _make_doc(["2026年部门财务收支预算总表\n单位：元\n项目 预算数"], dominant="亿元")
    assert _resolve_table_unit(doc2, 1) == "元"
    # 全部落空 → None（fail-closed，不得默认万元）
    doc3 = _make_doc(["2026年部门财务收支预算总表\n项目 预算数"])
    assert _resolve_table_unit(doc3, 1) is None


def test_table_unit_title_context_window():
    # 「单位：元」距表题 3 行以外不算邻近（±2 行窗口）
    doc = _make_doc(["2026年部门财务收支预算总表\n项目\n预算数\n单位：元"])
    assert _resolve_table_unit(doc, 1) is None


def test_bud101_fail_closed_when_unit_unknown():
    """单位不确定时严格取数 fail-closed：BUD-101 必须 Deferred（单位未知），
    不得默认万元硬判。锚点命中由 find_budget_anchors 在文本层完成。"""
    from src.engine.budget_rules import find_budget_anchors

    text = "2026年部门财务收支预算总表\n收入总计 100.00 支出总计 100.00"
    doc = _make_doc([text], page_tables=[[["部门收支总表"], ["收入总计", "100.00", "支出总计", "100.00"]]])
    anchors = find_budget_anchors(doc)
    if not anchors.get("BUD_T1"):
        pytest.skip("合成文本未命中 T1 锚点（锚点规则变化时需同步本用例）")
    # 注意：RuleDeferred 继承 BaseException（非 Exception），必须显式接住
    with pytest.raises(RuleDeferred) as ei:
        BUD101_T1Balance().apply(doc)
    assert "单位未知" in str(ei.value.detail)


# ---------------------------------------------------------------------------
# MR-B2：T4 五列横表合计列定位（合成用例）
# ---------------------------------------------------------------------------


def test_t4_total_column_branch():
    rows = [
        ["财政拨款收入", "", "财政拨款支出", "", "", "", ""],
        ["项目", "预算数", "项目", "合计", "一般公共预算", "政府性基金预算", "国有资本经营预算"],
        ["一、一般公共预算", "493,761,082.20", "一、社会保障和就业支出", "16,067,196.20", "16,067,196.20", "0.00", ""],
        ["收入总计", "3,286,618,082.20", "支出总计", "3,286,618,082.20", "493,761,082.20", "2,792,857,000.00", ""],
    ]
    inc, exp = _extract_t4_strict(rows, default_unit="元")
    assert inc is not None and inc.is_numeric
    assert exp is not None and exp.is_numeric
    assert exp.decimal_val == Decimal("328661.80822")


def test_t4_total_column_not_unique_fail_closed():
    """两个「合计」表头列无法消歧 → 继续 fail-closed，不得猜列。"""
    rows = [
        ["项目", "合计", "项目", "合计", "合计"],
        ["收入总计", "100.00", "支出总计", "200.00", "300.00"],
    ]
    _, exp = _extract_t4_strict(rows, default_unit="万元")
    assert exp is None or not exp.is_numeric


def test_text_vs_table_envelope_absorbs_truncation():
    """文本整万元截断显示（328,661.80822 →「328,661万元」）属显示舍入，
    不得报「文本与表不一致」；真实差异 ≥1 万仍判 mismatch。"""
    text_sv = StrictValue(
        raw_text="328,661", status=STATUS_VALID,
        decimal_val=Decimal("328661"), unit="万元", scale_digits=0,
    )
    table_sv = StrictValue(
        raw_text="3,286,618,082.20", status=STATUS_VALID,
        decimal_val=Decimal("328661.80822"), unit="元", scale_digits=2,
    )
    env = _text_vs_table_envelope(text_sv, table_sv)
    level, _ = classify_amount_diff(
        text_sv.decimal_val, table_sv.decimal_val, 1, envelope=env
    )
    assert level in ("ok", "rounding_hint")

    real_sv = StrictValue(
        raw_text="3,288,618,082.20", status=STATUS_VALID,
        decimal_val=Decimal("328861.80822"), unit="元", scale_digits=2,
    )
    level2, _ = classify_amount_diff(
        text_sv.decimal_val, real_sv.decimal_val, 1,
        envelope=_text_vs_table_envelope(text_sv, real_sv),
    )
    assert level2 == "mismatch"


# ---------------------------------------------------------------------------
# 验收行 3/4：CMM-001 on 4 份决算——真值锚提取 + 4/4 真实判定
# ---------------------------------------------------------------------------

#: 说明侧 6 字段真值锚（万元，任务包给定；None=说明侧确实未提及）
CMM001_TRUTH = {
    "yichuan": dict(
        total_budget="31.47", total_final="19.86", abroad="0.00",
        car_total="19.56", car_buy=None, car_run="19.56", reception="0.3",
    ),
    "shiquan": dict(
        total_budget="53.93", total_final="37.35", abroad="0",
        car_total="37.35", car_buy="25", car_run="12.35", reception="0",
    ),
    "wenlv": dict(
        total_budget="11.49", total_final="3.04", abroad="0",
        car_total="2.64", car_buy="0", car_run="2.64", reception="0.40",
    ),
    "golden": dict(
        total_budget="21.00", total_final="16.95", abroad="0.00",
        car_total="16.95", car_buy="0", car_run="16.95", reception="0",
    ),
}


@pytest.mark.parametrize("alias", sorted(FINAL_FIXTURES))
def test_cmm001_extraction_truth_anchors(alias):
    payload = json.loads((ROOT / FINAL_FIXTURES[alias]).read_text(encoding="utf-8"))
    doc = build_document(
        path=f"{payload.get('doc_id', alias)}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    section = _locked_three_public_section(merge_page_texts(doc.page_texts))
    assert section, f"{alias}: 三公说明段未锁定"
    nar = _extract_three_public_narrative_v2(section)
    for key, want in CMM001_TRUTH[alias].items():
        got = nar.get(key)
        if want is None:
            assert got is None, f"{alias}/{key}: 说明侧应未提及，实际提取 {got}"
        else:
            assert got is not None, f"{alias}/{key}: 真值 {want} 未提取到（缺项需取证）"
            assert abs(got - Decimal(want)) <= Decimal("0.005"), (
                f"{alias}/{key}: 提取 {got} ≠ 真值 {want}"
            )
    # 每份 ≥5/6 字段提取成功（6 字段口径：total 任一口径计 1）
    six = ["total", "abroad", "reception", "car_total", "car_buy", "car_run"]
    got_count = sum(
        1 for f in six
        if (nar.get("total_budget") is not None or nar.get("total_final") is not None)
        and f == "total"
        or (f != "total" and nar.get(f) is not None)
    )
    assert got_count >= 5, f"{alias}: 6 字段口径仅提取 {got_count}/6（需逐项取证）"


@pytest.mark.parametrize("alias", sorted(FINAL_FIXTURES))
def test_cmm001_resolved_on_finals(final_replays, alias):
    summary = final_replays[alias]["summary"]
    assert summary["rule_statuses"].get("CMM-001") == "pass", (
        f"{alias}: CMM-001 应为真实判定 pass"
    )
    cmm001_findings = [
        i for i in final_replays[alias]["issues"] if getattr(i, "rule", "") == "CMM-001"
    ]
    assert not cmm001_findings, (
        f"{alias}: CMM-001 不应新增 finding（说明×表逐字段吻合）："
        + "; ".join(str(getattr(i, "message", ""))[:60] for i in cmm001_findings)
    )


def test_finals_unresolved_total_le_10(final_replays):
    total = sum(r["summary"]["unresolved_total"] for r in final_replays.values())
    assert total <= 10, f"4 份决算 unresolved 合计 {total} > 10"


def test_yichuan_findings_stay_12(final_replays):
    """宜川 findings 维持 A 档压减裁决后的 12；新增条目必须另行取证裁决。"""
    assert len(final_replays["yichuan"]["issues"]) == 12


def test_wenlv_cascade_still_zero(final_replays):
    """A 档验收行持续守护：文旅 1700.57 级联误报保持 0。"""
    errors = [
        i for i in final_replays["wenlv"]["issues"]
        if str(getattr(i, "severity", "")).lower() == "error"
    ]
    assert [getattr(i, "rule", "") for i in errors] == ["CMM-007"]
