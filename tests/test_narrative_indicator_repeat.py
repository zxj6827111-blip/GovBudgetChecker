"""V33-NARRATIVE-INDICATOR-REPEAT：文内同一指标重复披露一致性（同段/跨段）。

真值来源（冻结，不得改来迁就实现）
--------------------------------
- **S03（高，曾漏报）**：石泉路街道 2025 年度决算（44 页）
  源 PDF SHA ``e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4``
  P32 职业年金条目首句 257.14 万元 vs P33 同条目「支出决算为 245.53 万元」，
  差 11.61；完成率 93.57% 对应 257.14，245.53/274.82 仅 89.34%（人工判定原文）。
- **Y07（低，曾漏报）**：宜川路街道 2025 年度决算（41 页，与 WP4-A/B 共用冻结夹具）
  源 PDF SHA ``f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03``
  P31 条目 15：首句 269.85 vs「支出决算为 269.86」，差 0.01——人工判定
  「属于同一指标抄写不一致，不是合计舍入尾差」；差值恰在两侧显示精度的
  动态舍入包络内 → 本规则按 info 报告（不静默、不升 error）。
- **真实负例**：生态环境局样张（说明条目自洽）→ 本规则不得制造 finding。

夹具口径（诚实声明）：
- 两条真实 truth 与生态负例之外，反例 A~G / 歧义 / 通用词拒绝表用
  **合成 contract fixture**（不是真实样张，业务身份按 docs/WP4C 设计文档构造）。

这里只断言**业务事实**：两处披露值、差额、页码、章节、严重度、以及
"不报什么"。不把实现细节（绑定正则、归一方式）写进期望值。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_NOT_IMPLEMENTED,
    build_obligation_ledger,
    registered_rule_ids,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.rule_outcome import STATUS_FAIL, RuleDeferred  # noqa: E402
from src.engine.rules_v33 import (  # noqa: E402
    R33NarrativeIndicatorRepeat,
    build_document,
)

RULE_ID = "V33-NARRATIVE-INDICATOR-REPEAT"
OBLIGATION_ID = "OBL-NARRATIVE-INDICATOR-REPEAT"

#: 石泉路街道 2025 年度决算（S03 真值所在文件；生产管线同口径提取冻结）
SHIQUAN_FIXTURE = ROOT / "tests" / "fixtures" / "shiquan_narrative_truth_page_data.json"
SHIQUAN_SOURCE_SHA = "e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4"
SHIQUAN_FIXTURE_SHA = "be8abd4685bc0de3fc2b87b91e0f3d32e79a50e369c9e4dc7aa3cfe5c28272d7"

#: 宜川路街道 2025 年度决算（Y07 真值；与 WP4-A/B 同一份冻结夹具，SHA 双锁）
YICHUAN_FIXTURE = ROOT / "tests" / "fixtures" / "cross_san_gong_truth_page_data.json"
YICHUAN_SOURCE_SHA = "f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03"
YICHUAN_FIXTURE_SHA = "45d761f3e3b7dc2ac023f1224bf4f02f7496860cca33b9f925ac8da991dcab4c"

#: 生态环境局样张（真实"无基金/国资"负例，此处用作"文内自洽不得造假"负例）
SAMPLE_FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"
SAMPLE_SOURCE_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"
SAMPLE_FIXTURE_SHA = "2f862419dde3d04326125c9f4c45f9171c9c45af303c49a350b3be998ec31de1"

#: 两份真值材料里该章节的实体标题（P27 起始）
SECTION_EXPEND = "五、一般公共预算财政拨款支出决算情况说明"


# ---------------------------------------------------------------------------
# 夹具装载（fail-closed：缺失或不符即失败，不静默跳过）
# ---------------------------------------------------------------------------


def _load_fixture(path: Path, source_sha: str, fixture_sha: str) -> Dict[str, Any]:
    assert path.exists(), f"固定夹具缺失: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == fixture_sha, (
        f"夹具内容哈希不符（{path.name}）——夹具被修改或需重新生成"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_pdf_sha256"] == source_sha, f"夹具源 PDF SHA 不符（{path.name}）"
    return payload


@pytest.fixture(scope="module")
def shiquan() -> Dict[str, Any]:
    """石泉路街道 2025 年度决算（S03 漏报的冻结真值样张）。"""
    return _load_fixture(SHIQUAN_FIXTURE, SHIQUAN_SOURCE_SHA, SHIQUAN_FIXTURE_SHA)


@pytest.fixture(scope="module")
def yichuan() -> Dict[str, Any]:
    """宜川路街道 2025 年度决算（Y07 漏报的真值样张；WP4-A/B 同源冻结）。"""
    return _load_fixture(YICHUAN_FIXTURE, YICHUAN_SOURCE_SHA, YICHUAN_FIXTURE_SHA)


@pytest.fixture(scope="module")
def clean_sample() -> Dict[str, Any]:
    """生态环境局 2025 年度决算（文内自洽的真实负例）。"""
    return _load_fixture(SAMPLE_FIXTURE, SAMPLE_SOURCE_SHA, SAMPLE_FIXTURE_SHA)


def _document(payload: Dict[str, Any]) -> Any:
    return build_document(
        path=f"{payload.get('doc_id', 'sample')}.pdf",
        page_texts=list(payload["page_texts"]),
        page_tables=json.loads(json.dumps(payload.get("page_tables", []))),
        filesize=0,
    )


def _run(doc: Any) -> List[Any]:
    return list(R33NarrativeIndicatorRepeat().apply(doc))


def _contract_doc(*sections: str) -> Any:
    """合成 contract 文档：页 1 提供年度锚，其后每参量为一页叙事文本。"""
    return build_document(
        path="contract-narrative-repeat.pdf",
        page_texts=["某某局 2025 年度部门决算公开", *sections],
        page_tables=[],
        filesize=0,
    )


def _anqi_page(*clauses: str) -> str:
    """把合同用例放进同一个决算支出说明章节（同章节才允许裸名互比）。"""
    return SECTION_EXPEND + "\n" + "\n".join(clauses)


def _indicator_issues(issues: List[Any], keyword: str) -> List[Any]:
    return [
        item
        for item in issues
        if keyword in (getattr(item, "message", "") or "")
        or keyword in str((getattr(item, "location", None) or {}).get("indicator", ""))
    ]


# ---------------------------------------------------------------------------
# 真实 truth 正例
# ---------------------------------------------------------------------------


def test_s03_truth_reports_the_conflict(shiquan):
    """S03（高）：石泉 P32 条目首句 257.14 vs P33 支出决算为 245.53，必须报 error。"""
    issues = _run(_document(shiquan))

    hits = _indicator_issues(issues, "职业年金")
    assert len(hits) == 1, [getattr(i, "message", "") for i in issues]
    issue = hits[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "error", "同一指标两处披露差 11.61 万元不得降级"

    location = issue.location
    assert location["indicator"] == "机关事业单位职业年金缴费支出"
    assert location["measure"] == "本年支出决算"
    assert location["period"] == "2025 年度"
    assert location["unit"] == "万元"
    assert location["fiscal_year"] == 2025
    assert location["obligation_id"] == OBLIGATION_ID
    assert sorted({location["value_a"], location["value_b"]}) == ["245.53", "257.14"]
    assert location["difference"] == "11.61"
    assert location["pages"] == [32, 33], "两处披露必须分别定位到 P32 与 P33"
    refs = {ref["role"]: ref for ref in location["table_refs"]}
    assert set(refs) == {"披露A", "披露B"}
    assert refs["披露A"]["page"] == 32 and "257.14" in refs["披露A"]["span"]
    assert refs["披露B"]["page"] == 33 and "245.53" in refs["披露B"]["span"]
    assert refs["披露A"]["section"] == SECTION_EXPEND
    assert refs["披露B"]["section"] == SECTION_EXPEND


def test_s03_message_does_not_claim_which_side_is_correct(shiquan):
    """任务 §十六：只说两处不能同时成立，不判断哪一处是正确值。"""
    issues = _run(_document(shiquan))
    issue = _indicator_issues(issues, "职业年金")[0]
    assert "不能同时成立" in issue.message
    assert "正确" not in issue.message.replace("确认正确金额", "")


def test_y07_truth_reports_envelope_level_info(yichuan):
    """Y07（低）：宜川 P31 同条目 269.85 vs 269.86，差 0.01 → info（包络内）。"""
    issues = _run(_document(yichuan))

    hits = _indicator_issues(issues, "职业年金")
    assert len(hits) == 1, [getattr(i, "message", "") for i in issues]
    issue = hits[0]
    assert issue.severity == "info", "0.01 差在显示舍入包络内：报告但不升 error"

    location = issue.location
    assert sorted({location["value_a"], location["value_b"]}) == ["269.85", "269.86"]
    assert location["difference"] == "0.01"
    assert location["pages"] == [31]
    assert "可能为取整误差" in issue.message


def test_truth_pipeline_reports_s03_in_full_final_run(shiquan):
    """真实 truth pipeline：完整 final 规则集执行必须产出本规则的 fail 结论。

    Mutation C 的守卫：把 V33-NARRATIVE-INDICATOR-REPEAT 从注册表拿掉后，
    这条测试必须红（finding 消失 = 漏报回归）。
    """
    doc = _document(shiquan)
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind="final")

    assert RULE_ID in registered_rule_ids("final")
    rule_codes = {outcome.rule_id for outcome in outcomes}
    assert RULE_ID in rule_codes, "final 规则集未执行本规则——注册表与执行集脱节"
    outcome = next(item for item in outcomes if item.rule_id == RULE_ID)
    assert outcome.status == STATUS_FAIL
    assert any(
        issue.rule == RULE_ID and "职业年金" in issue.message for issue in issues
    ), "完整 final 执行未保留 S03 finding（Contract C1：fail 的 finding 不得被丢弃）"


# ---------------------------------------------------------------------------
# 真实负例
# ---------------------------------------------------------------------------


def test_shiquan_self_consistent_entries_stay_silent(shiquan):
    """石泉其余条目首句==支出决算为（252.74/220.79/545.84/5.43/200.18/…）→ 不报。"""
    issues = _run(_document(shiquan))
    for amount in ("252.74", "220.79", "545.84", "200.18", "177.17", "7.11", "5.43"):
        assert not _indicator_issues(issues, amount), (
            f"自洽条目（首句==支出决算为 {amount}）不得产生重复披露 finding"
        )
    # 全文档只有 S03 一组真实冲突
    assert len(issues) == 1


def test_ecological_bureau_sample_reports_nothing(clean_sample):
    """真实负例：生态环境局样张文内自洽 → 本规则 0 finding。"""
    issues = _run(_document(clean_sample))
    assert issues == [], [getattr(i, "message", "") for i in issues]


# ---------------------------------------------------------------------------
# 合同负例 A~G（合成 contract fixture，非真实样张）
# ---------------------------------------------------------------------------


def test_case_a_current_vs_prior_year_is_not_a_conflict():
    """反例 A：本年 vs 上年是不同期间，绝不能因为 257.14 != 245.53 报冲突。"""
    doc = _contract_doc(_anqi_page("本年职业年金257.14万元，上年职业年金245.53万元。"))
    assert _run(doc) == []


def test_case_b_current_vs_delta_is_not_a_conflict():
    """反例 B：本年金额 vs 增加额/增长率，完全合法的同比句。"""
    doc = _contract_doc(
        _anqi_page(
            "本年职业年金257.14万元，比上年245.53万元增加11.61万元，增长4.73%。",
            "职业年金本年257.14万元，增加11.61万元。",
        )
    )
    assert _run(doc) == []


def test_case_c_actual_vs_budget_is_not_a_conflict():
    """反例 C：预算 vs 决算是不同 measure，不得比较。"""
    doc = _contract_doc(_anqi_page("职业年金预算245.53万元，决算257.14万元。"))
    assert _run(doc) == []


def test_case_d_unit_normalization_equal_values():
    """反例 D：257.14万元 与 2571400元 归一后同值 → 0 finding。"""
    doc = _contract_doc(
        _anqi_page("本年职业年金缴费支出257.14万元。", "本年职业年金缴费支出2571400元。")
    )
    assert _run(doc) == []


def test_case_e_different_indicators_never_pair():
    """反例 E：职业年金与养老保险是不同指标，不比较。"""
    doc = _contract_doc(
        _anqi_page("本年职业年金257.14万元。", "本年养老保险245.53万元。")
    )
    assert _run(doc) == []


def test_case_f_same_indicator_same_value_silent():
    """反例 F：同指标、同口径、同金额的两处披露 → 0 finding。"""
    doc = _contract_doc(
        _anqi_page("职业年金缴费支出257.14万元。", "职业年金缴费支出257.14万元。")
    )
    assert _run(doc) == []


def test_case_g_same_indicator_different_values_is_a_finding():
    """正例 G：同指标、同口径（本年支出决算）、同单位、不同金额 → 正式 finding。"""
    doc = _contract_doc(
        _anqi_page("职业年金缴费支出257.14万元。", "职业年金缴费支出245.53万元。")
    )
    issues = _run(doc)
    assert len(issues) == 1, [getattr(i, "message", "") for i in issues]
    issue = issues[0]
    assert issue.rule == RULE_ID
    assert issue.severity == "error"
    location = issue.location
    assert location["indicator"] == "职业年金缴费支出"
    assert sorted({location["value_a"], location["value_b"]}) == ["245.53", "257.14"]
    assert location["difference"] == "11.61"
    refs = {ref["role"]: ref for ref in location["table_refs"]}
    assert "257.14" in refs["披露A"]["span"] and "245.53" in refs["披露B"]["span"]
    assert refs["披露A"]["section"] == SECTION_EXPEND


# ---------------------------------------------------------------------------
# 身份与 fail-closed
# ---------------------------------------------------------------------------


def test_foreign_year_clause_is_not_bound():
    """期间锚：句内出现与材料年度不同的显式年份 → 不跨期绑定、不比较。

    任务 §九：2024 年职业年金 245.53 与 2025 年职业年金 257.14 不能报错。
    """
    doc = _contract_doc(
        _anqi_page("2025年本年职业年金257.14万元。", "2024年本年职业年金245.53万元。")
    )
    assert _run(doc) == []


def test_generic_structural_terms_are_never_paired():
    """通用结构词拒绝表：「基本支出/项目支出/人员经费」在文内合法地多次
    出现不同数值（总口径 vs 明细），不得按同名指标配对。

    注：合同金额避开 20XX 形态（「2000 万元」会被共享年份启发式识别为年份
    2000，与 2025 并列最高频 → 年度解析按 tie-guard fail-closed，那是另一条
    设计行为，不在这里测）。
    """
    doc = _contract_doc(
        _anqi_page(
            "基本支出14206万元。基本支出12010万元。",
            "项目支出1200万元。项目支出950万元。",
        )
    )
    assert _run(doc) == []


def test_class_level_name_shadowing_p1_is_not_paired():
    """动态拒绝表：裸名等于本文档任一类/款级名称（P1 条目已证明是结构类目）
    → 不按裸名互比（同名类级数字天然可能指不同下级口径）。"""
    doc = _contract_doc(
        _anqi_page(
            "五、一般公共预算财政拨款支出决算情况说明",
            "“住房保障支出（类）城乡社区住宅（款）其他城乡社区住宅支出（项）”307.95 万元，"
            "主要用于：公房。年初预算为 616.08 万元，支出决算为 307.95 万元。",
            "住房保障支出100万元。住房保障支出200万元。",
        )
    )
    issues = _run(doc)
    assert not _indicator_issues(issues, "住房保障支出100") and all(
        "100" not in str((getattr(i, "location", None) or {}).get("value_a", ""))
        and "100" not in str((getattr(i, "location", None) or {}).get("value_b", ""))
        for i in issues
    ), "类级裸名不得互比（真实冲突只有 P1 条目内的自洽比较，此处应为 0 finding）"
    assert issues == []


def test_ambiguous_double_amount_blocks_binding():
    """任务 §十九：同句两个都可能是指标金额的数字 → parse_ambiguity，
    禁止取最近/取首个/取最大。"""
    doc = _contract_doc(_anqi_page("本年职业年金245.53万元和257.14万元。"))
    with pytest.raises(RuleDeferred) as excinfo:
        _run(doc)
    assert excinfo.value.status == "insufficient_data"
    detail = f"{excinfo.value.detail} {' '.join(excinfo.value.unresolved_reasons or [])}"
    assert "候选金额" in detail or "歧义" in detail
    assert excinfo.value.partial_issues == []


def test_cross_section_bare_names_are_not_aggregated():
    """任务 §十：越跨章节身份要求越严——裸名不跨章节聚合。"""
    doc = _contract_doc(
        _anqi_page("职业年金缴费支出257.14万元。"),
        SECTION_EXPEND + "\n职业年金缴费支出245.53万元。",
    )
    assert _run(doc) == []


def test_rule_is_final_only_not_in_budget_registry():
    """任务 §二十一：registered_rule_ids('final') 含、('budget') 不含。"""
    assert RULE_ID in registered_rule_ids("final")
    assert RULE_ID not in registered_rule_ids("budget")


# ---------------------------------------------------------------------------
# 台账收口（Mutation C 的固化守卫）
# ---------------------------------------------------------------------------


def _profile_receipt(rule_status: str) -> Dict[str, Any]:
    """台账回执：与生产 rule_execution_summary 同构（rule_statuses 包装）。"""
    return {"rule_statuses": {RULE_ID: rule_status}}


def test_obligation_gap_is_closed_and_receipt_drives_status():
    """缺口收口：不再 not_implemented；回执 pass/fail → completed（任务 §二十九）。"""
    assert RULE_ID in registered_rule_ids("final")

    from src.engine.check_obligations import (
        OBLIGATION_COMPLETED,
        OBLIGATION_NOT_IMPLEMENTED,
    )

    def _instance(ledger: Dict[str, Any]) -> Dict[str, Any]:
        items = [
            item
            for item in ledger["instances"]
            if item["obligation_id"] == OBLIGATION_ID
        ]
        assert items, "台账缺少 OBL-NARRATIVE-INDICATOR-REPEAT 实例"
        return items[0]

    # 预算文种不含该义务（final only）
    budget_ledger = build_obligation_ledger(None, report_kind="budget")
    assert not [
        i for i in budget_ledger["instances"] if i["obligation_id"] == OBLIGATION_ID
    ]

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = _instance(ledger)
    assert instance["status"] != OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == []

    completed = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("fail")
    )
    receipt = _instance(completed)
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert f"{RULE_ID}=fail" in receipt["detail"]

    insufficient = build_obligation_ledger(
        None, report_kind="final", rule_execution_summary=_profile_receipt("insufficient_data")
    )
    deferred = _instance(insufficient)
    assert deferred["status"] != OBLIGATION_COMPLETED, (
        "checker 取数不足时义务不得自动完成（继续阻塞人工补核）"
    )


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """Mutation C 自动化：规则从注册表撤下 → 义务必须回到 not_implemented。

    否则"缺口数下降"可能来自清单改动，而不是真实能力增加。
    """
    from src.engine import rules_v33

    monkeypatch.setattr(
        rules_v33,
        "ALL_RULES",
        [
            rule
            for rule in rules_v33.ALL_RULES
            if getattr(rule, "code", "") != RULE_ID
        ],
    )
    assert RULE_ID not in registered_rule_ids("final")

    ledger = build_obligation_ledger(None, report_kind="final")
    instance = next(
        item for item in ledger["instances"] if item["obligation_id"] == OBLIGATION_ID
    )
    assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == [RULE_ID]
