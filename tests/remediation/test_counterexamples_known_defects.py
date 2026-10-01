"""评估报告反例的缺陷索引（ZCode 整改任务包 §四.1）。

本文件把 2026-10-01 可用性评估中已复现的规则反例与合并/定位反例
固化为仓库测试。未修复项标 ``xfail(strict=True)``；所属阶段修复后移除标记，
转为普通回归：

- 每条反例注明归属整改阶段（ZC-02/ZC-03/ZC-04）；
- 归属阶段修复后测试转为 XPASS，strict 模式会让套件失败，
  提醒删除标记并把测试转为正式回归；
- 证据类反例（无页码兜底、999 页、生成说明当原文）已在 ZC-00 修复，
  作为必须通过的用例放在 ``test_zc00.py``，不在本文件。

禁止通过删除本文件或放宽断言制造通过。反例输入全部为合成数据，
不依赖 outputs/ 或原始 PDF。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import pytest

from pydantic import ValidationError

from src.engine.budget_rules import (
    BUD001_StructureAndAnchors,
    BUD107_TextTableConsistency,
    _get_budget_table_rows,
)
from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import build_document
from src.schemas.issues import AnalysisConfig, IssueItem
from src.services.merge_findings import FindingsMerger

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _run_one(doc: Any, rule: Any) -> Dict[str, Any]:
    issues, outcomes = run_rules_with_outcomes(doc, report_kind="budget", rules=[rule])
    return {
        "outcomes": [o.to_dict() for o in outcomes],
        "issues": [
            {
                "rule_id": i.rule,
                "severity": i.severity,
                "message": i.message,
                "location": i.location,
            }
            for i in issues
        ],
    }


def _build_pair(ai_severity: str, rule_severity: str, ai_amount: float = 100, rule_amount: float = 100):
    shared: Dict[str, Any] = dict(
        title="同一标题",
        message="检测金额不一致",
        location={"page": 2},
        tags=["R"],
        check_relation_id="C-001:total",
        fact_ids=["fact-three-public-total"],
    )
    ai = IssueItem(
        id="AI",
        source="ai",
        severity=ai_severity,
        evidence=[{"page": 2, "text": "弱证据"}],
        metrics={"amount": ai_amount},
        **shared,
    )
    rule = IssueItem(
        id="RULE",
        source="rule",
        severity=rule_severity,
        evidence=[{"page": 2, "text": "原始单元格证据"}],
        metrics={"amount": rule_amount},
        **shared,
    )
    return ai, rule


# ---------------------------------------------------------------------------
# 规则反例（评估报告 §四.2～§四.4、rules_repro.json 五例）
# ---------------------------------------------------------------------------


def test_fixed_zc02_largest_table_misbinding() -> None:
    """目标表较小、无关采购表较大：应选目标表，不选最大表。"""
    small = [["项目", "预算数", "项目", "预算数"], ["收入总计", "100.00", "支出总计", "100.00"]]
    large = [
        ["无关项目表", "金额"],
        ["甲", "500.00"],
        ["乙", "500.00"],
        ["合计", "1000.00"],
    ]
    doc = build_document(
        "budget.pdf", ["2026年部门财务收支预算总表\n单位：万元"], [[small, large]], 0
    )
    rows, _page = _get_budget_table_rows(doc, {"BUD_T1": [1]}, "BUD_T1")
    assert rows == small, "必须选中目标表而不是同页最大的无关表"


def test_fixed_zc02_next_page_different_table_merged_as_continuation() -> None:
    """下一页是另一张表：不得拼成续表。

    触发路径（budget_rules._get_budget_table_rows）：目标页表格没有合计行时，
    直接追加下一页"最大表"且不核对下一页表身份。
    """
    target_page = [
        ["项目", "预算数"],
        ["办公经费", "100.00"],
        ["印刷费", "20.00"],
    ]
    other_page = [
        ["政府采购预算表", "金额"],
        ["办公设备购置", "60.00"],
        ["合计", "60.00"],
    ]
    doc = build_document(
        "budget.pdf",
        ["2026年部门收支预算表", "政府采购预算表"],
        [[target_page], [other_page]],
        0,
    )
    rows, _page = _get_budget_table_rows(doc, {"BUD_T1": [1]}, "BUD_T1")
    for row in other_page:
        assert row not in rows, "另一张表的行不得被拼进目标表"


@pytest.mark.xfail(
    strict=True,
    reason="SYS-03：BUD-107 全文首次匹配金额，上年说明会与本年表比较（归属 ZC-03 事实绑定）",
)
def test_xfail_previous_year_narrative_misbinding() -> None:
    """上年 213012 万元出现在本年材料前部：不得发生跨年比较。"""
    payload = pytest.importorskip("json")
    fixture_path = REPO_ROOT / "tests" / "fixtures" / "jianguanwei_budget_page_data.json"
    data = payload.loads(fixture_path.read_text(encoding="utf-8"))
    texts: List[str] = deepcopy(data["page_texts"])
    tables = deepcopy(data["page_tables"])
    baseline = _run_one(
        build_document("budget.pdf", texts, tables, 0), BUD107_TextTableConsistency()
    )
    texts[0] += "\n历史情况：2025年收入预算213012万元，支出预算213012万元。"
    mutated = _run_one(
        build_document("budget.pdf", texts, tables, 0), BUD107_TextTableConsistency()
    )
    assert baseline["issues"] == mutated["issues"], (
        "追加明确的上年历史金额不得影响本年比较（跨年误报）"
    )


def test_fixed_zc02_three_public_column_order() -> None:
    """合法列序「合计、出国、公车小计、购置、运行、接待」不得产生错配报警。"""
    text = (
        "2026年部门“三公”经费和机关运行经费预算表\n单位：万元\n"
        "三公经费预算30.00万元；因公出国（境）费0.00万元；公务用车购置及运行费20.00万元；"
        "公务用车购置费0.00万元；公务用车运行费20.00万元；公务接待费10.00万元。"
    )
    table = [
        [
            "合计",
            "因公出国（境）费",
            "公务用车购置及运行费",
            "购置费",
            "运行费",
            "公务接待费",
            "机关运行经费",
        ],
        ["30.00", "0.00", "20.00", "0.00", "20.00", "10.00", "50.00"],
    ]
    output = _run_one(
        build_document("budget.pdf", [text], [[table]], 0),
        CMM001_ThreePublicNarrativeConsistency(),
    )
    mismatch_keywords = ("不一致", "不匹配", "错配")
    assert not any(
        any(keyword in str(issue["message"]) for keyword in mismatch_keywords)
        for issue in output["issues"]
    ), f"合法列序不得产生错配报警：{output['issues']}"


@pytest.mark.xfail(
    strict=True,
    reason="SYS-05：三公只核对合计即记 pass，分项未比对被假通过（归属 ZC-03 子检查回执）",
)
def test_xfail_partial_comparison_reported_pass() -> None:
    """文字只披露三公合计 30、表内分项完整：合计可完成，分项子检查不得假通过。

    复现自 rules_repro 反例 5：正文风格与反例 4 相同但只写合计金额，
    CMM-001 未定位到分项说明就直接返回 pass，无任何未完成原因。
    """
    text = "2026年部门“三公”经费和机关运行经费预算表\n单位：万元\n三公经费预算30.00万元。"
    table = [
        [
            "合计",
            "因公出国（境）费",
            "公务用车购置及运行费",
            "购置费",
            "运行费",
            "公务接待费",
            "机关运行经费",
        ],
        ["30.00", "0.00", "20.00", "0.00", "20.00", "10.00", "50.00"],
    ]
    output = _run_one(
        build_document("budget.pdf", [text], [[table]], 0),
        CMM001_ThreePublicNarrativeConsistency(),
    )
    assert output["outcomes"], "规则必须产生执行回执"
    for outcome in output["outcomes"]:
        if outcome.get("status") == "pass":
            assert outcome.get("unresolved_reasons"), (
                "分项（出国/公车/接待）未与说明比对时不得记完整 pass，必须带未完成原因"
            )


@pytest.mark.xfail(
    strict=True,
    reason="BUD-001 固定九表与 portable 条件性政策冲突（归属 ZC-03 条件性资金规则）",
)
def test_xfail_project_policy_conditional_tables() -> None:
    """材料无政府性基金/国资经营内容：不得无条件报缺表。"""
    from src.engine.budget_rules import BUDGET_TABLE_SPECS

    titles = [
        s["aliases"][0] for s in BUDGET_TABLE_SPECS if s["key"] not in {"BUD_T6", "BUD_T7"}
    ]
    doc = build_document("budget.pdf", titles, [[] for _ in titles], 0)
    output = _run_one(doc, BUD001_StructureAndAnchors())
    conditional_hits = [
        i
        for i in output["issues"]
        if any(key in str(i["message"]) for key in ("BUD_T6", "BUD_T7"))
    ]
    assert not conditional_hits, "无正面适用证据时不得直接判条件性表缺失"


# ---------------------------------------------------------------------------
# 合并/定位反例（merge_repro.json；证据两例已在 ZC-00 修复）
# ---------------------------------------------------------------------------


def test_fixed_zc06_weaker_ai_does_not_replace_stronger_rule() -> None:
    """AI medium vs 规则 high：合并代表项不得丢失规则来源的更强证据。"""
    ai, rule = _build_pair("medium", "high")
    merged = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])
    assert merged.merged_ids == ["RULE"] or "RULE" in merged.merged_ids, (
        "合并代表项必须保留更强规则条目，而不是恒选 AI"
    )


def test_fixed_zc06_value_conflict_is_not_also_counted_as_agreement() -> None:
    """AI 100 vs 规则 90：冲突不得同时统计为一致。"""
    ai, rule = _build_pair("high", "high", 100, 90)
    merged = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])
    assert len(merged.conflicts) == 1
    assert len(merged.agreements) == 0, "同一匹配对冲突与一致互斥"


def test_fixed_zc06_severity_conflict_is_reviewed_without_batch_failure() -> None:
    """AI medium vs 规则 high：严重度冲突不得计为一致，必须进入冲突/待复核。

    当前行为（2026-10-01 实测）：severity 不同的匹配对既不记冲突也不降级，
    直接出现在 agreements 里——评估报告所述"severity-mismatch 枚举校验失败
    引发整批回退"在当前代码已变为"静默并入一致"，同属冲突键/枚举缺陷族。
    """
    ai, rule = _build_pair("medium", "high")
    try:
        merged = FindingsMerger(AnalysisConfig()).merge_findings([ai], [rule])
    except ValidationError:
        pytest.fail("严重度冲突不得触发 ValidationError / 整批回退")
    assert len(merged.agreements) == 0, (
        f"严重度冲突不得计为一致：agreements={merged.agreements}"
    )
    assert len(merged.conflicts) == 1, "严重度冲突必须进入冲突列表/待复核"


def test_zc04_table_title_fallback_does_not_return_bbox_without_target_fact(tmp_path) -> None:
    """只找到表名、目标数字不存在：只能区域导航，不得伪装精确 bbox。

    反例：页面上有表名「收入决算表」但不含目标行「本年收入合计 888.00」——
    修复后兜底命中不得产出精确 bbox（修复态：bbox 为 None）。
    """
    fitz = pytest.importorskip("fitz")
    from src.utils.issue_bbox import PDFBBoxLocator

    pdf_path = None
    try:
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "收入决算表", fontsize=14, fontname="china-s")
        page.insert_text((72, 100), "其他支出 12.00 万元", fontsize=11, fontname="china-s")
        pdf_path = str(tmp_path / "fallback_probe.pdf")
        document.save(pdf_path)
        document.close()

        issue = IssueItem(
            id="case-6",
            source="rule",
            rule_id="V33-201",
            severity="high",
            title="本年收入合计不一致",
            message="本年收入合计 888.00 与表格不一致",
            location={"page": 1, "table": "收入决算表", "row": "本年收入合计"},
            evidence=[{"page": 1, "text": "本年收入合计 888.00"}],
            page_number=1,
        )
        located = PDFBBoxLocator(pdf_path).locate(issue)
        # 修复态：目标数字不存在于页面，兜底不得拼出看似精确的 bbox
        assert located.bbox is None, (
            "目标数字不存在时不得用表名/首匹配兜底拼出精确 bbox"
        )
    finally:
        if pdf_path and Path(pdf_path).exists():
            Path(pdf_path).unlink()
