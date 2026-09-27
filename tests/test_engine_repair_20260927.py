"""MR-1/MR-2/MR-3（2026-09-27「查得准」A 档）验收测试。

方案：车道 1（解析层止血 + V33-120 降噪 + 扫描件误报闸门）。

全部基于冻结页数据夹具离线重放（不解析 PDF、不调 AI），量化验收行：
- 4 份决算 unresolved 规则数 53 → ≤15（实测 14；快照双锁）
- V33-101~108 在 4/4 样张 unresolved 清零；V33-101 支出恒等式在
  golden 样张上可执行（insufficient → pass）
- 文旅局 1700.57 级联误报（V33-005×2 + V33-202 error）→ 0 条；
  解析结构性矛盾按 MR-1c 哨兵转 insufficient_data（人工复核）
- 宜川输出总数 34 → 13。方案目标 ≤12，差 1 条的构成说明：13 条中
  5 error + 1 medium + 5 info 为 WP4-A/B/D/E/F/G 设计内真实发现
  （跨表三公、基金说明、零基数同比、披露规范类），1 warn 为 V33-227
  命中的真实科目名不一致（说明"文化体育与传媒支出" vs 表格
  "文化旅游体育与传媒支出"，归一空白后核对原文确认），1 info 为
  V33-120 聚类结果。MR-1 红线是不动这些车道的设计行为。
- V33-120 舍入聚类：宜川 23 条 info → 1 条（count 标注、明细保留）
- MR-3 扫描闸门：error 级整体转人工；可读性正常时严格 no-op

夹具哈希沿用 WP4-H 的换行归一口径（autocrlf 双锁免疫，PR #40 教训）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from src.engine.pipeline import (  # noqa: E402
    apply_readability_gate,
    build_document,
    run_rules_with_outcomes,
)
from src.engine.rule_outcome import (  # noqa: E402
    UNRESOLVED_STATUSES,
    summarize_rule_outcomes,
)
from src.engine.rules_v33 import (  # noqa: E402
    _get_table_rows,
    _row_value_v2,
    _total_structural_contradiction,
)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 冻结夹具与验收快照（换行归一 SHA，autocrlf 免疫）
# ---------------------------------------------------------------------------

REPLAY_FIXTURES = {
    "yichuan": "tests/fixtures/cross_san_gong_truth_page_data.json",
    "shiquan": "tests/fixtures/shiquan_narrative_truth_page_data.json",
    "wenlv": "tests/fixtures/wenlv_narrative_truth_page_data.json",
    "golden": "tests/fixtures/sample_page_data.json",
}

FIXTURE_SHAS = {
    "yichuan": "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c",
    "shiquan": "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7",
    "wenlv": "07813ae61b04cf9f3afd2bf93c69b357ff9aeefee3ba6a893b34d77d956e8252",
    "golden": "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1",
}

BEFORE_SNAPSHOT = ROOT / "tests/fixtures/engine_repair_estimate_before_20260927.json"
AFTER_SNAPSHOT = ROOT / "tests/fixtures/engine_repair_estimate_after_20260927.json"
BEFORE_SHA = "e447a31325ce88692d6cded6f86a3d591ad5c2c93c56a6b4c9d5c56ef8d61831"
AFTER_SHA = "9d2352e9fc1edae0a1f4326b6c0bdd22e042c010e27683bae80f33e2047fb53c"

#: V33-101~108：本改造的核心取数修复面（4 份样张 unresolved 必须清零）
CORE_RULES = [f"V33-10{i}" for i in range(1, 9)]


def _norm_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _load_snapshot(path: Path, expected_sha: str) -> Dict[str, Any]:
    assert path.exists(), f"验收快照缺失: {path}"
    assert _norm_sha(path) == expected_sha, f"快照哈希不符（{path.name}）——需重新生成"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 模块级一次性重放（4 份样张 × 全量规则）
# ---------------------------------------------------------------------------


def _replay(alias: str) -> Dict[str, Any]:
    fixture = ROOT / REPLAY_FIXTURES[alias]
    assert _norm_sha(fixture) == FIXTURE_SHAS[alias], f"冻结夹具哈希不符: {fixture.name}"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    doc = build_document(
        path=f"{payload.get('doc_id', alias)}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload["page_tables"])),
        filesize=0,
    )
    issues, outcomes = run_rules_with_outcomes(
        doc, use_ai_assist=False, report_kind="final"
    )
    summary = summarize_rule_outcomes(outcomes)
    unresolved = {
        item.get("rule_id"): item
        for item in summary["unresolved_rules"]
        if item.get("status") in UNRESOLVED_STATUSES
    }
    return {
        "doc": doc,
        "issues": issues,
        "summary": summary,
        "unresolved": unresolved,
    }


@pytest.fixture(scope="module")
def replays() -> Dict[str, Dict[str, Any]]:
    return {alias: _replay(alias) for alias in REPLAY_FIXTURES}


# ---------------------------------------------------------------------------
# 快照双锁
# ---------------------------------------------------------------------------


def test_acceptance_snapshots_pinned():
    """修复前=53、修复后快照入库且哈希锁定（验收基线可追溯）。"""
    before = _load_snapshot(BEFORE_SNAPSHOT, BEFORE_SHA)
    after = _load_snapshot(AFTER_SNAPSHOT, AFTER_SHA)
    assert before["totals"]["unresolved_total"] == 53
    assert after["totals"]["unresolved_total"] == 14


# ---------------------------------------------------------------------------
# MR-1 验收：unresolved 53 → ≤15；V33-101~108 清零；恒等式可执行
# ---------------------------------------------------------------------------


def test_unresolved_total_le_15(replays):
    assert len(CORE_RULES) == 8
    total = sum(len(r["unresolved"]) for r in replays.values())
    assert total <= 15, f"unresolved 总数 {total} 超出验收线 15"
    after = _load_snapshot(AFTER_SNAPSHOT, AFTER_SHA)
    assert total == after["totals"]["unresolved_total"], (
        "unresolved 总数与验收快照不符——引擎行为的计划外变更需重走验收"
    )


@pytest.mark.parametrize("alias", list(REPLAY_FIXTURES.keys()))
def test_core_rules_v33_101_to_108_resolved(replays, alias):
    """V33-101~105 在 4/4 样张清零；106/107/108 同样清零，唯一例外是
    文旅局的 V33-106——其 T5 解析结构性矛盾由 MR-1c 哨兵转人工，
    属于验收设计行为而非取数失败。"""
    unresolved = replays[alias]["unresolved"]
    leaked = [rule for rule in CORE_RULES if rule in unresolved]
    if alias == "wenlv":
        assert leaked == ["V33-106"]
        assert "疑似解析错位" in str(
            unresolved["V33-106"].get("unresolved_reasons") or ""
        )
    else:
        assert not leaked, f"{alias}: {leaked} 仍 unresolved"


def test_v33_101_identity_executes_on_golden(replays):
    """golden 样张上 V33-101 支出恒等式必须真实执行（不再是 insufficient）。"""
    unresolved = replays["golden"]["unresolved"]
    assert "V33-101" not in unresolved
    assert "V33-101" in replays["golden"]["summary"]["rule_statuses"]
    assert replays["golden"]["summary"]["rule_statuses"]["V33-101"] == "pass"


def test_v33_240_241_deferral_preserved_on_golden(replays):
    """方案红线：V33-240/241 的跨页列漂移拒答行为仍合法存在。"""
    unresolved = replays["golden"]["unresolved"]
    assert "V33-240" in unresolved and "V33-241" in unresolved


# ---------------------------------------------------------------------------
# MR-1 验收：文旅局 1700.57 级联误报清零
# ---------------------------------------------------------------------------


def test_wenlv_cascade_errors_zero(replays):
    issues = replays["wenlv"]["issues"]
    errors = [i for i in issues if str(getattr(i, "severity", "")).lower() == "error"]
    cascade = [
        i
        for i in errors
        if getattr(i, "rule", "") in {"V33-005", "V33-106", "V33-202", "V33-222"}
        or "1700.57" in str(getattr(i, "message", ""))
    ]
    assert not cascade, f"文旅局级联误报仍在: {[str(getattr(i, 'message', ''))[:60] for i in cascade]}"
    # 唯一保留的 error 是 CMM-007（WP4-D 设计内零基数同比真阳性）
    assert [getattr(i, "rule", "") for i in errors] == ["CMM-007"]
    # 哨兵路径：矛盾面转 insufficient_data 转人工，而非 error
    assert "V33-202" in replays["wenlv"]["unresolved"]


# ---------------------------------------------------------------------------
# MR-1 验收：宜川输出总数 34 → ≤13（方案目标 ≤12，构成说明见模块 docstring）
# ---------------------------------------------------------------------------


def test_yichuan_output_budget(replays):
    assert len(replays["yichuan"]["issues"]) <= 13


# ---------------------------------------------------------------------------
# MR-2 验收：V33-120 舍入聚类（23 条 info → 1 条，明细保留）
# ---------------------------------------------------------------------------


def test_v33_120_rounding_clustered(replays):
    issues = replays["yichuan"]["issues"]
    v120 = [i for i in issues if getattr(i, "rule", "") == "V33-120"]
    assert len(v120) == 1, f"宜川 V33-120 应聚类为 1 条，实际 {len(v120)}"
    finding = v120[0]
    assert finding.location.get("count") == 23
    assert "明细" in str(getattr(finding, "message", ""))
    # 原始条目保留在证据明细（可追溯到表/科目）
    assert "收入决算表" in str(getattr(finding, "evidence_text", ""))
    assert "支出决算表" in str(getattr(finding, "evidence_text", ""))
    assert "一般公共预算财政拨款支出决算表" in str(getattr(finding, "evidence_text", ""))


def test_v33_120_mismatch_still_reported_on_wenlv(replays):
    """文旅 T5 层级/列合计检查随整表哨兵转人工，不产出 error/warn 假层级告警。"""
    issues = replays["wenlv"]["issues"]
    bad = [
        i
        for i in issues
        if getattr(i, "rule", "") == "V33-120"
        and str(getattr(i, "severity", "")).lower() in {"error", "warn"}
    ]
    assert not bad


# ---------------------------------------------------------------------------
# MR-1a/MR-1c 单元：_row_value_v2 三阶段兜底 + 结构矛盾哨兵
# ---------------------------------------------------------------------------


def test_row_value_v2_dual_side_same_half():
    """T6 表尾双侧行：row[0] 命中后必须取标签同侧值（旧 rightmost 交叉错配）。"""
    table = [
        ["301", "工资福利支出", "11,860.23", "302", "商品和服务支出", "1,844.57"],
        ["人员经费合计", "", "12,192.15", "公用经费合计", "", "1,915.30"],
    ]
    assert _row_value_v2(table, ("人员经费合计", "人员经费")) == 12192.15
    assert _row_value_v2(table, ("公用经费合计", "公用经费")) == 1915.30


def test_row_value_v2_right_side_label():
    """T1 收支总表：右半区标签（side="right"）取右半值，总计行取支出列总计。"""
    table = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "26,538.47", "本年支出合计", "26,538.47"],
        ["使用非财政拨款结余", "", "结余分配", ""],
        ["年初结转和结余", "", "年末结转和结余", "52.61"],
        ["总计", "26,591.08", "总计", "26,591.08"],
    ]
    bn = _row_value_v2(table, ("本年支出合计", "本年支出", "本年合计"), side="right")
    jy = _row_value_v2(table, ("结余分配", "结余分配支出"), side="right", empty_as_zero=True)
    jz = _row_value_v2(
        table, ("年末结转和结余", "年末结转", "结转结余"), side="right", empty_as_zero=True
    )
    total = _row_value_v2(table, ("支出总计", "总计"), side="right")
    assert bn == 26538.47
    assert jy == 0.0  # 空白=0（empty_as_zero，恒等式自身兜底）
    assert jz == 52.61
    assert total == 26591.08
    # 文旅恒等式：本年支出 + 结余分配 + 年末结转 = 支出总计
    assert abs((bn + jy + jz) - total) < 0.01


def test_row_value_v2_header_column_fallback():
    """T2/T3 表头式布局：财政拨款收入/基本支出是列头，值在合计行与列的交点。"""
    income_table = [
        ["项目", "", "本年收入合计", "财政拨款收入", "上级补助收入"],
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["合计", "", "20323.21", "20323.21", ""],
        ["201", "群众团体事务", "37.51", "37.51", ""],
    ]
    assert (
        _row_value_v2(
            income_table,
            ("本年收入合计", "本年合计", "合计"),
            prefer_cols=("本年收入合计", "本年合计"),
        )
        == 20323.21
    )
    assert _row_value_v2(income_table, ("财政拨款收入",)) == 20323.21

    expense_table = [
        ["项目", "", "本年支出合计", "基本支出", "项目支出"],
        ["功能分类科目编码", "科目名称", "", "", ""],
        ["合计", "", "20,323.21", "14,107.46", "6,215.76"],
        ["201", "一般公共服务支出", "207.40", "", "207.40"],
    ]
    assert _row_value_v2(expense_table, ("基本支出",)) == 14107.46
    assert _row_value_v2(expense_table, ("项目支出",)) == 6215.76


def test_row_value_v2_label_found_but_empty_without_zero_optin():
    """默认不把"标签命中但值空"当 0（防假勾稽纪律）；显式 opt-in 才返回 0。"""
    table = [
        ["本年收入合计", "100.00", "本年支出合计", "100.00"],
        ["使用非财政拨款结余", "", "结余分配", ""],
    ]
    assert _row_value_v2(table, ("结余分配",), side="right") is None
    assert _row_value_v2(table, ("结余分配",), side="right", empty_as_zero=True) == 0.0


def test_total_structural_contradiction_sentinel():
    """MR-1c：合计 < 表内最大分项 = 解析矛盾；科目编码列不计入分项。"""
    corrupted = [
        ["项目", "", "合计", "基本支出", "项目支出"],
        ["207", "文化旅游体育与传媒支出", "17,127.62", "7,374.19", "9,753.43"],
        ["合计", "", "1,700.57", "", "1,700.57"],
    ]
    assert _total_structural_contradiction(corrupted, 1700.57) is True

    healthy = [
        ["项目", "", "合计", "基本支出", "项目支出"],
        ["2010507", "专项普查活动", "39.99", "", "39.99"],
        ["合计", "", "20,218.21", "14,107.46", "6,110.76"],
    ]
    # 编码列（2010507）不得当成 201 万级分项
    assert _total_structural_contradiction(healthy, 20218.21) is False
    assert _total_structural_contradiction([], 100.0) is False
    assert _total_structural_contradiction(healthy, None) is False


# ---------------------------------------------------------------------------
# MR-1b 回归锁：anchor_extent 合并不得原地污染 doc.page_tables
# ---------------------------------------------------------------------------


def test_anchor_extent_does_not_mutate_doc_tables():
    """_largest_table_on_page 返回的就是 doc.page_tables 里的列表对象；
    合并必须构造新列表（全量回归时 T5 rows 翻三倍的实测教训）。"""
    doc_tables = [
        # 页 1（锚点页）：1 张表 × 1 行
        [[["208", "社会保障和就业支出", "1,972.20", "", "1,972.20"]]],
        # 页 2（续页）：1 张表 × 1 行
        [[["20805", "机关事业单位养老", "4.84", "", "4.84"]]],
    ]
    doc = SimpleNamespace(
        page_tables=doc_tables,
        page_texts=["一般公共预算财政拨款支出决算表", ""],
        anchors={"一般公共预算财政拨款支出决算表": [1]},
    )
    merged = _get_table_rows(doc, "一般公共预算财政拨款支出决算表", anchor_extent=True)
    assert len(merged) == 2
    # 关键断言：doc.page_tables 内的表对象行数不变
    assert len(doc.page_tables[0][0]) == 1
    assert len(doc.page_tables[1][0]) == 1
    # 再次调用结果一致（无累积污染）
    merged_again = _get_table_rows(
        doc, "一般公共预算财政拨款支出决算表", anchor_extent=True
    )
    assert len(merged_again) == 2


# ---------------------------------------------------------------------------
# MR-3 验收：扫描件可读性闸门
# ---------------------------------------------------------------------------


def _gated_error(rule: str, message: str) -> Dict[str, Any]:
    return {
        "id": f"{rule}-1",
        "source": "rule",
        "rule": rule,
        "rule_id": rule,
        "severity": "error",
        "title": message[:20],
        "message": message,
        "evidence": [],
        "location": {"page": 1, "pos": 0},
        "bbox": None,
        "suggestion": "",
        "tags": [rule],
        "metrics": {},
        "created_at": 0,
        "rule_version": "v33",
        "model_version": None,
        "prompt_version": None,
        "engine_version": "test",
    }


def test_scan_gate_converts_errors_to_manual_review():
    payload = {
        "issues": {
            "error": [
                _gated_error("V33-002", "缺少核心表：收入支出决算总表"),
                _gated_error("V33-101", "总表支出恒等式不成立"),
            ],
            "warn": [],
            "info": [],
        }
    }
    assessment = {"page_coverage": 0.0, "scanned_page_count": 10}
    out = apply_readability_gate(payload, assessment)
    assert out["issues"]["error"] == []
    assert len(out["issues"]["warn"]) == 1
    manual = out["issues"]["warn"][0]
    assert manual["rule"] == "READABILITY-GATE"
    assert manual["severity"] == "manual_review"
    assert len(out["readability_gate"]["suppressed"]) == 2
    assert out["readability_gate"]["suppressed"][0]["rule"] == "V33-002"


def test_scan_gate_noop_for_readable_docs():
    """可读性正常（覆盖率达标且无扫描页）时严格 no-op——历史行为不变。"""
    payload = {
        "issues": {
            "error": [_gated_error("V33-101", "总表支出恒等式不成立")],
            "warn": [],
            "info": [],
        }
    }
    original = json.dumps(payload, ensure_ascii=False)
    out = apply_readability_gate(payload, {"page_coverage": 1.0, "scanned_page_count": 0})
    assert json.dumps(out, ensure_ascii=False) == original
    # 未提供 page_assessment 时同样 no-op
    out2 = apply_readability_gate(payload, None)
    assert out2["issues"]["error"]
