"""检查义务账本测试：应检查的事情完成了多少，以及完成率为什么不能被绿化。

本文件的分量集中在"防绿化"上。plan §3 明确要求：

- 应执行事项未完成，整体结论必须保留 review_required / incomplete；
- 非适用项必须有依据，不能为了提高完成率随意排除；
- 不能通过删规则、跳过输入、把缺失变零或批量降级，让完成率变绿。

因此除了正常路径，每个"看起来能让完成率变好看"的手法都有一条反向用例。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine import check_obligations  # noqa: E402
from src.engine.check_obligations import (  # noqa: E402
    OBLIGATION_AI_NOT_RUN,
    OBLIGATION_COMPLETED,
    OBLIGATION_INSUFFICIENT_DATA,
    OBLIGATION_KIND_CONFLICT,
    OBLIGATION_NOT_APPLICABLE,
    OBLIGATION_NOT_EXECUTED,
    OBLIGATION_NOT_IMPLEMENTED,
    OBLIGATION_PARSE_AMBIGUITY,
    SAN_GONG_CHECK_OBLIGATION_IDS,
    SAN_GONG_OBLIGATION_IDS,
    build_obligation_ledger,
    obligation_ids_for_rule,
    registered_rule_ids,
    validate_catalog,
)
from src.schemas.document_profile import DocumentProfile  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402


def _profile(kind: str = "final", level: str | None = None) -> DocumentProfile:
    # 页面文本与文种保持同向：决算文本会把 budget profile 判回 final
    # （kind_disagreement），budget 用例必须喂预算文本。
    doc_noun = "部门决算" if kind == "final" else "部门预算"
    label_line = {
        "unit": "决算单位：某街道办事处",
        "department": "决算主管部门：某局",
        "government": "2024年某区政府决算",
    }.get(level or "", "")
    pages = [f"2024年度{doc_noun}\n{label_line}"] if label_line else [f"2024年度{doc_noun}"]
    profile = resolve_document_profile(
        doc_type="dept_final" if kind == "final" else "dept_budget",
        filename=f"2024年度{doc_noun}.pdf",
        page_texts=pages,
    )
    assert profile.kind == kind
    return profile


def _receipt(statuses: dict[str, str] | None = None) -> dict:
    return {"rule_statuses": dict(statuses or {})}


def _all_pass(kind: str) -> dict:
    return _receipt({code: "pass" for code in registered_rule_ids(kind)})


def _instance(ledger: dict, obligation_id: str) -> dict:
    for item in ledger["instances"]:
        if item["obligation_id"] == obligation_id:
            return item
    raise AssertionError(f"obligation {obligation_id} not found in ledger")


# ---------------------------------------------------------------------------
# 清单自检
# ---------------------------------------------------------------------------


def test_catalog_is_self_consistent():
    assert validate_catalog() == []


@pytest.mark.parametrize("kind", ["budget", "final"])
def test_declared_checkers_exist_in_the_real_rule_registry(kind):
    """清单里声明的 checker 必须在真实注册表里存在。

    这条断言防的是一个真实踩过的坑：清单曾引用 ``V33-210`` / ``V33-211``，
    而这两条规则已从 ``ALL_RULES`` 撤下。若不做校验，它们会被报成
    "尚未实现"——真实缺口被假缺口淹没，真缺口反而不会被处理。
    """
    registry = registered_rule_ids(kind)
    for obligation in check_obligations.OBLIGATION_CATALOG:
        if kind not in obligation.report_kinds or obligation.requires_ai:
            continue
        for code in obligation.checkers_for(kind):
            assert code in registry, f"{obligation.obligation_id} 引用了不存在的规则 {code}"


@pytest.mark.parametrize("kind", ["budget", "final"])
def test_declared_gaps_are_genuinely_unimplemented(kind):
    """声明为缺口（pending_checkers）的编号必须确实没有实现。

    反过来也一样：一个"缺口"如果其实已经有规则实现了，它就必须被删掉，
    否则完成率永远上不去，是另一种形式的数据失真。
    """
    registry = registered_rule_ids(kind)
    for obligation in check_obligations.OBLIGATION_CATALOG:
        if kind not in obligation.report_kinds or obligation.requires_ai:
            continue
        for code in obligation.pending_checkers:
            if kind not in obligation.report_kinds:
                continue
            assert code not in registry, (
                f"{obligation.obligation_id} 把已实现的 {code} 声明成了缺口"
            )


def test_san_gong_is_expanded_into_six_instances():
    """plan §3 点名要求：核验三公经费不能只记一条完成。

    合计、分项、预算完成率、同比、表文一致性、披露要素必须各自记账。
    """
    assert len(SAN_GONG_CHECK_OBLIGATION_IDS) == 6
    assert set(SAN_GONG_CHECK_OBLIGATION_IDS) <= set(SAN_GONG_OBLIGATION_IDS)

    ledger = build_obligation_ledger(_profile("final"), report_kind="final")
    san_gong = [
        item for item in ledger["instances"] if item["group_id"] == "SAN_GONG"
    ]
    present = {item["obligation_id"] for item in san_gong}
    assert set(SAN_GONG_CHECK_OBLIGATION_IDS) <= present
    # 决算口径下三公分组的适用实例数不少于六个（六个必需实例都在决算适用）
    assert len([item for item in san_gong if item["status"] != "not_applicable"]) >= 6


# ---------------------------------------------------------------------------
# 状态推导
# ---------------------------------------------------------------------------


def test_all_rules_pass_leaves_only_implementation_gaps():
    # WP4-G 收口后 final 车道已无"尚未实现"缺口（coverage gaps 2→1，
    # 剩余缺口 OBL-PERF-PHASE-AMOUNT 在 budget 车道）。
    final_ledger = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_all_pass("final")
    )
    assert set(final_ledger["by_reason"]) <= {"ai_not_run"}, (
        "final 出现非 AI 未执行的未完成——出现新的实现缺口或六态错记"
    )
    assert final_ledger["by_reason"].get("not_implemented", 0) == 0
    assert final_ledger["completed_total"] == (
        final_ledger["applicable_total"] - final_ledger["unresolved_total"]
    )

    budget_ledger = build_obligation_ledger(
        _profile("budget"),
        report_kind="budget",
        rule_execution_summary=_all_pass("budget"),
    )
    assert budget_ledger["by_reason"]["not_implemented"] > 0
    assert budget_ledger["completed_total"] == (
        budget_ledger["applicable_total"] - budget_ledger["unresolved_total"]
    )


def test_fail_counts_as_completed_check_with_findings():
    """规则命中（fail）说明检查**做了**，问题另计。

    把 fail 当成"未完成"会让"发现问题越多、完成率越低"，从而反向激励
    漏报；把 pass 当完成、fail 当未完成都是错的。这里的口径是：六态里的
    pass 与 fail 都算检查已完成，只有缺数据/解析失败/异常才算未完成。
    """
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"V33-121": "fail", "V33-244": "pass"}),
    )
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["status"] == OBLIGATION_COMPLETED
    assert total["checkers"] == ["V33-121", "V33-244"]


@pytest.mark.parametrize(
    ("rule_status", "expected"),
    [
        ("insufficient_data", OBLIGATION_INSUFFICIENT_DATA),
        ("parse_error", OBLIGATION_PARSE_AMBIGUITY),
        ("execution_error", "execution_error"),
    ],
)
def test_unresolved_rule_states_map_to_unfinished_obligation(rule_status, expected):
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"V33-121": rule_status, "V33-244": "pass"}),
    )
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["status"] == expected
    assert total["reason"] == expected
    assert total["blocks_gate"] is True


def test_missing_receipt_is_not_treated_as_pass():
    """没有回执就是"没执行"，不能默认通过。

    这是假绿最常见的入口：摘要缺失时如果默认当完成，那么规则进程被跳过、
    超时、崩溃都会表现为"检查通过"。
    """
    ledger = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_receipt({})
    )
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["status"] == OBLIGATION_NOT_EXECUTED
    assert total["blocks_gate"] is True


def test_partial_receipts_do_not_complete_obligation():
    """独立验收 2026-09-17 partial_receipt 反例：缺一条回执不得记完成。

    OBL-TREND-DIRECTION 依赖 CMM-006 与 V33-245，只提供 CMM-006=pass 时
    旧逻辑整单记 completed，文案甚至写"两条规则已得出可信结论"。整改后
    必须逐条核对所需 checker：缺哪条回执就点名哪条，并保持阻塞。
    """
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"CMM-006": "pass"}),
    )
    direction = _instance(ledger, "OBL-TREND-DIRECTION")
    assert direction["status"] == OBLIGATION_NOT_EXECUTED
    assert direction["blocks_gate"] is True
    assert "V33-245" in direction["detail"]
    assert "OBL-TREND-DIRECTION" in ledger["blocking_obligation_ids"]


def test_unknown_receipt_status_is_not_a_valid_terminal_state():
    """回执状态不在六态内时同样不算完成：未知状态不能被当成 pass 消费。"""
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"CMM-006": "pass", "V33-245": "unfinished"}),
    )
    direction = _instance(ledger, "OBL-TREND-DIRECTION")
    assert direction["status"] == OBLIGATION_NOT_EXECUTED
    assert direction["blocks_gate"] is True
    assert "V33-245" in direction["detail"]


def test_mixed_pass_and_not_applicable_stays_in_denominator_with_detail():
    """部分 pass、部分不适用：检查已执行，但必须逐条写明结果。

    "部分不适用"若被读成"整单不适用"，该实例会掉出完成率分母——
    这正是用 not_applicable 绿化完成率的手法，必须用明细堵住。
    """
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt(
            {"CMM-006": "pass", "V33-245": "not_applicable"}
        ),
    )
    direction = _instance(ledger, "OBL-TREND-DIRECTION")
    assert direction["status"] == OBLIGATION_COMPLETED
    assert "CMM-006=pass" in direction["detail"]
    assert "V33-245=not_applicable" in direction["detail"]


def test_fail_on_one_checker_still_counts_the_check_as_done():
    """多 checker 义务里一条 fail、其余 pass：检查已完成，问题另计。"""
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"CMM-006": "fail", "V33-245": "pass"}),
    )
    direction = _instance(ledger, "OBL-TREND-DIRECTION")
    assert direction["status"] == OBLIGATION_COMPLETED


def test_zero_base_recompute_requires_cmm007_receipt_not_cmm005():
    """零基数复算由 CMM-007 承担；CMM-005 的回执不得被读作该能力。

    独立验收 2026-09-17：OBL-TREND-COMPARATIVE-LOGIC 曾宣称"零基数不得
    表述为增长百分比"，但 CMM-005 只查"当前为 0 却写增加"。样张漏报
    （宜川接待费 0.30/增加 0.30、文旅 0.40/增加 0.40，均写"增长100%"）
    证明该子能力不存在，遂拆出 OBL-TREND-ZERO-BASE 并登记缺口。
    WP4-D（2026-09-25）落地 CMM-007 后缺口收口：能力真实存在，但完成
    必须由 CMM-007 自己的回执驱动——只有 CMM-005=pass 时不得 completed。
    """
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"CMM-005": "pass"}),
    )
    instance = _instance(ledger, "OBL-TREND-ZERO-BASE")
    assert instance["status"] != OBLIGATION_COMPLETED, (
        "CMM-005 的回执不得被读作零基数复算能力（2026-09-17 验收教训）"
    )

    completed = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"CMM-005": "pass", "CMM-007": "fail"}),
    )
    done = _instance(completed, "OBL-TREND-ZERO-BASE")
    assert done["status"] == OBLIGATION_COMPLETED
    # 拆分后，CMM-005 的完成不再被读作"零基数复算已具备"
    logic = _instance(completed, "OBL-TREND-COMPARATIVE-LOGIC")
    assert logic["status"] == OBLIGATION_COMPLETED
    assert "零基数不得表述为增长百分比" not in logic["basis"]


def test_kind_conflict_is_registered_as_a_blocking_obligation():
    """独立验收 2026-09-17：画像记录了文种冲突，台账必须单独记账并阻塞。

    显式指定 final、文件名与正文都是"部门预算"时，按优先级取的 final
    只是候选选择。若台账不登记，报告里写着"需人工确认"，质量门却显示
    检查完整，冲突永远到不了人工面前。
    """
    profile = resolve_document_profile(
        explicit_report_kind="final",
        filename="2024部门预算.pdf",
        page_texts=["2024年度部门预算"],
    )
    assert profile.profile_status == "partial"
    assert profile.unsupported_reason == "report_kind_conflict"

    ledger = build_obligation_ledger(profile, report_kind="final")
    conflict = _instance(ledger, "OBL-PROFILE-KIND-CONFLICT")
    assert conflict["status"] == OBLIGATION_KIND_CONFLICT
    assert conflict["blocks_gate"] is True
    assert "OBL-PROFILE-KIND-CONFLICT" in ledger["blocking_obligation_ids"]
    assert ledger["by_reason"][OBLIGATION_KIND_CONFLICT] == 1
    # 冲突记账不挤占正常义务：专项义务照常展开
    assert _instance(ledger, "OBL-SG-TOTAL")


def test_kind_conflict_detail_names_only_truly_conflicting_candidates():
    """冲突明细只点名互斥候选，同向的落选证据不列入。

    与所选值相同的落选候选是"同向证据"（如 page_text 与显式值一致），
    混进明细会把冲突份量和一致份量混在一起，人工复核反而更难判断。
    """
    profile = resolve_document_profile(
        explicit_report_kind="final",
        filename="2024部门预算.pdf",
        page_texts=["2024年度部门决算"],
    )
    ledger = build_obligation_ledger(profile, report_kind="final")
    conflict = _instance(ledger, "OBL-PROFILE-KIND-CONFLICT")
    assert "filename=budget" in conflict["detail"], "冲突的 filename 候选必须点名"
    assert "page_text=" not in conflict["detail"], "同向的 page_text 候选不应出现在冲突明细"


def test_rule_not_in_registry_is_reported_as_unimplemented():
    # WP4-G 后 OBL-SG-COMPLETION 已收口，改用剩余唯一缺口
    # OBL-PERF-PHASE-AMOUNT 作缺口示例（coverage gaps 2→1）。
    ledger = build_obligation_ledger(_profile("budget"), report_kind="budget")
    gap = _instance(ledger, "OBL-PERF-PHASE-AMOUNT")
    assert gap["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert gap["missing_checkers"] == ["BUD-PERF-PHASE-AMOUNT"]
    assert gap["gap_note"]


def test_implemented_checker_is_no_longer_a_gap_and_records_its_receipt():
    """WP4-A：OBL-CROSS-SAN-GONG-ECON 的实现缺口已补齐。

    这条义务此前是 ``not_implemented``（checker 只写在 pending_checkers 里）。
    现在 checker 真实注册：无回执时是 ``not_executed``（跑了才有结论），
    有回执时按回执落状态，不再报"尚未实现"。
    """
    assert "V33-CROSS-SAN-GONG-ECON" in registered_rule_ids("final")

    instance = _instance(
        build_obligation_ledger(_profile("final"), report_kind="final"),
        "OBL-CROSS-SAN-GONG-ECON",
    )
    assert instance["status"] == OBLIGATION_NOT_EXECUTED
    assert instance["missing_checkers"] == []
    assert instance["gap_note"] == ""
    assert set(instance["depends_on"]) == {"table:FIN_06", "table:FIN_07"}

    receipt = _instance(
        build_obligation_ledger(
            _profile("final"),
            report_kind="final",
            rule_execution_summary=_receipt({"V33-CROSS-SAN-GONG-ECON": "fail"}),
        ),
        "OBL-CROSS-SAN-GONG-ECON",
    )
    assert receipt["status"] == OBLIGATION_COMPLETED
    assert "V33-CROSS-SAN-GONG-ECON=fail" in receipt["detail"]


def test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap(monkeypatch):
    """变异验证：规则从注册表撤下 → 该义务必须回到 not_implemented。

    否则"缺口数下降"可能来自清单改动，而不是真实能力增加。
    """
    from src.engine import rules_v33

    monkeypatch.setattr(
        rules_v33,
        "ALL_RULES",
        [
            rule
            for rule in rules_v33.ALL_RULES
            if getattr(rule, "code", "") != "V33-CROSS-SAN-GONG-ECON"
        ],
    )
    assert "V33-CROSS-SAN-GONG-ECON" not in registered_rule_ids("final")
    instance = _instance(
        build_obligation_ledger(_profile("final"), report_kind="final"),
        "OBL-CROSS-SAN-GONG-ECON",
    )
    assert instance["status"] == OBLIGATION_NOT_IMPLEMENTED
    assert instance["missing_checkers"] == ["V33-CROSS-SAN-GONG-ECON"]


def test_ai_obligation_does_not_block_unless_ai_is_required():
    """AI 语义义务未执行必须如实记账，但只在 AI 被配置为必需时才阻塞门禁。

    理由见模块 docstring：纯规则模式是用户显式选择，此时让语义复核的
    缺失一律阻塞会凭空制造人工复核量。
    """
    not_required = build_obligation_ledger(
        _profile("final"), report_kind="final", ai_execution=None, ai_required=False
    )
    ai_instance = _instance(not_required, "OBL-AI-SEMANTIC-REVIEW")
    assert ai_instance["status"] == OBLIGATION_AI_NOT_RUN
    assert ai_instance["blocks_gate"] is False
    assert not_required["by_reason"]["ai_not_run"] == 1
    assert "OBL-AI-SEMANTIC-REVIEW" not in not_required["blocking_obligation_ids"]

    required = build_obligation_ledger(
        _profile("final"), report_kind="final", ai_execution=None, ai_required=True
    )
    assert "OBL-AI-SEMANTIC-REVIEW" in required["blocking_obligation_ids"]

    succeeded = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        ai_execution={"state": "succeeded"},
        ai_required=True,
    )
    assert _instance(succeeded, "OBL-AI-SEMANTIC-REVIEW")["status"] == OBLIGATION_COMPLETED


# ---------------------------------------------------------------------------
# 防绿化：四种"让完成率变好看"的手法都必须无效
# ---------------------------------------------------------------------------


def test_removing_rules_from_the_registry_does_not_improve_coverage(monkeypatch):
    """删规则不能让完成率变绿。

    完成率的分母来自义务清单（业务要求），不是规则注册数。若把最终规则
    集合整个清空，每一条义务都会变成"尚未实现"，仍是未完成。
    """
    baseline = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_all_pass("final")
    )
    before = baseline["coverage_rate"]

    monkeypatch.setattr(check_obligations, "_registry_rule_ids", lambda _kind: frozenset())
    stripped = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_all_pass("final")
    )

    assert stripped["applicable_total"] == baseline["applicable_total"], "分母不得随注册表变化"
    assert stripped["coverage_rate"] < before
    assert stripped["blocking_total"] > baseline["blocking_total"]
    assert stripped["by_reason"][OBLIGATION_NOT_IMPLEMENTED] > 0


def test_zero_denominator_yields_none_not_full_coverage():
    """分母为 0 时完成率必须是 None，不能是 100%。

    "没有应检查事项"与"检查完整"是两件事。文种未识别时若不登记显式未完成
    实例，台账分母会变成 0，完成率会被算成 1.0——漏查直接变成满分。
    """
    ledger = build_obligation_ledger(None, report_kind="unknown")
    assert ledger["applicable_total"] == 1
    assert ledger["coverage_rate"] == 0.0
    assert ledger["unresolved_total"] == 1
    assert ledger["blocking_total"] == 1
    instance = ledger["instances"][0]
    assert instance["obligation_id"] == "OBL-PROFILE-UNRESOLVED"
    assert instance["reason"] == "profile_unresolved"
    assert ledger["notes"]


def test_excluding_obligations_requires_a_reason():
    """非适用项必须带依据，不能靠"批量排除"提高完成率。

    只有两条路径能把义务移出分母：文种不适用（清单声明）+ 规则自身判定
    不适用（附规则编号）。两条都必须留下可读依据。
    """
    ledger = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        rule_execution_summary=_receipt({"V33-121": "not_applicable", "V33-244": "not_applicable"}),
    )
    for item in ledger["instances"]:
        if item["status"] != OBLIGATION_NOT_APPLICABLE:
            continue
        assert item["basis"], f"{item['obligation_id']} 被排除但缺少依据"
        assert item["detail"], f"{item['obligation_id']} 被排除但没有写明理由"
    # 规则判定不适用 -> 有依据地移出分母；但它仍是"已判定"而不是"没查"
    total = _instance(ledger, "OBL-SG-TOTAL")
    assert total["status"] == OBLIGATION_NOT_APPLICABLE
    assert "V33-121" in total["detail"]


def test_input_gaps_are_only_recorded_when_explicitly_reported():
    """输入缺口只认显式信号，不把"无法确认"写成"缺失"。

    入库侧明确报告缺表时记一笔 input_gaps；没有任何入库信息时留空，
    因为"不知道"与"缺失"是两件事，混为一谈会制造假缺口。
    """
    depends_on_obligation = "OBL-CROSS-T1-T2"  # depends_on FIN_01 / FIN_02
    without_ingest = build_obligation_ledger(_profile("final"), report_kind="final")
    assert _instance(without_ingest, depends_on_obligation)["input_gaps"] == []

    with_missing = build_obligation_ledger(
        _profile("final"),
        report_kind="final",
        structured_ingest={
            "review_items": [
                {"type": "missing_core_table", "table_code": "FIN_02"},
            ]
        },
    )
    gaps = _instance(with_missing, depends_on_obligation)["input_gaps"]
    assert gaps == ["table:FIN_02"]
    # 缺口只做诊断，不改变"这项检查跑没跑"的判定（判定只信规则回执）
    assert _instance(with_missing, depends_on_obligation)["status"] == OBLIGATION_NOT_EXECUTED
    # 有显式缺表信号时，未完成原因仍来自回执本身，input_gaps 供人定位
    unrelated = _instance(with_missing, "OBL-STRUCT-COVER")
    assert unrelated["input_gaps"] == []


def test_completing_every_obligation_reaches_full_coverage(monkeypatch):
    """反向边界：全部义务完成后完成率必须到 1.0（不是永远红）。

    否则"完成率"会失去判别力——永远为 0 的指标和永远为 100% 的指标一样没用。
    """
    implemented_only = tuple(
        item
        for item in check_obligations.OBLIGATION_CATALOG
        if not item.pending_checkers and not item.requires_ai
    )
    monkeypatch.setattr(
        check_obligations, "OBLIGATION_CATALOG", implemented_only, raising=True
    )
    ledger = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_all_pass("final")
    )
    assert ledger["unresolved_total"] == 0
    assert ledger["blocking_total"] == 0
    assert ledger["coverage_rate"] == 1.0
    assert ledger["auto_completion_rate"] == 1.0


# ---------------------------------------------------------------------------
# 分组统计与 finding 关联
# ---------------------------------------------------------------------------


def test_group_summary_reconciles_with_totals():
    ledger = build_obligation_ledger(
        _profile("final"), report_kind="final", rule_execution_summary=_all_pass("final")
    )
    assert sum(item["applicable"] for item in ledger["by_group"]) == ledger["applicable_total"]
    assert sum(item["unresolved"] for item in ledger["by_group"]) == ledger[
        "unresolved_total"
    ]
    assert sum(item["not_applicable"] for item in ledger["by_group"]) == ledger[
        "not_applicable_total"
    ]
    assert sum(ledger["by_reason"].values()) == ledger["unresolved_total"]


def test_reason_labels_cover_every_reported_reason():
    ledger = build_obligation_ledger(None, report_kind="budget")
    for code in ledger["by_reason"]:
        assert ledger["by_reason_labels"][code]
        assert ledger["by_reason_labels"][code] != code


def test_obligation_ids_for_rule_normalises_case_and_whitespace():
    assert obligation_ids_for_rule("v33-121") == obligation_ids_for_rule(" V33-121 ")
    assert obligation_ids_for_rule("V33-121")
    assert obligation_ids_for_rule("") == []
    assert obligation_ids_for_rule(None) == []
    # 未挂到任何义务的规则返回空表，而不是硬塞一个编号
    assert obligation_ids_for_rule("NO-SUCH-RULE") == []


def test_attach_obligation_ids_tags_both_dual_and_legacy_shapes():
    from src.engine.check_obligations import attach_obligation_ids

    result = {
        "ai_findings": [{"rule_id": "V33-121", "source": "rule"}],
        "rule_findings": [{"rule_id": "V33-244", "source": "rule"}],
        "issues": {
            "all": [
                {"rule_id": "V33-121", "source": "rule"},
                {"rule_id": "CMM-999", "source": "rule"},
            ],
            "error": [],
            "warn": [],
            "info": [],
        },
    }
    attached = attach_obligation_ids(result)
    assert attached == 3
    assert "OBL-SG-TOTAL" in result["issues"]["all"][0]["obligation_ids"]
    # 未挂到义务的规则不被打标签，避免制造虚假的业务分组
    assert "obligation_ids" not in result["issues"]["all"][1]
