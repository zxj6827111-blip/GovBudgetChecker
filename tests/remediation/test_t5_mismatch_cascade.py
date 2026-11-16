from __future__ import annotations

from decimal import Decimal
import re
from types import SimpleNamespace

from api.main import _evaluate_quality_gate
from src.engine.diagnostic_fields import (
    aggregate,
    diagnostics_for_texts,
    fields_for_text,
    record_gap,
    text_missing_operand,
)
from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import (
    _hierarchy_fact,
    _is_near_subject_name_candidate,
)
from src.engine.rules_v33 import (
    R33227_Narrative5_T5_NameConsistency,
    _tolerant_functional_item_codes,
    build_document,
)
from src.schemas.material_detail import CoverageObligationItem
from src.schemas.parse_artifact import FiscalFact
from src.services.obligation_finding_coverage import (
    cascade_root_issue_id,
    cascade_root_issue_ids,
    obligation_covered_by_finding,
)
from src.services.review_lifecycle_service import (
    blocking_coverage_obligations,
    cascaded_coverage_obligations,
)


def _t5_fact(code: str, name: str, amount: str, page: int = 12) -> FiscalFact:
    return FiscalFact(
        fact_uid=f"fact:{code}:{amount}",
        indicator="total_actual",
        raw_value=amount,
        unit="元",
        normalized_value=Decimal(amount) / Decimal("10000"),
        org_name="测试单位",
        fiscal_year=2025,
        subject_code=code,
        subject_name=name,
        table_code="FIN_05",
        recognition_status="verified",
        extra_dims={
            "evidence_refs": [{"physical_page": page, "table_code": "FIN_05"}],
        },
    )


def _v33227_issue(
    issue_id: str,
    *,
    code: str,
    actual_name: str,
    expected_name: str,
    page: int,
    severity: str = "warn",
    evidence_status: str | None = "verified",
) -> dict:
    return {
        "id": issue_id,
        "rule": "V33-227",
        "rule_id": "V33-227",
        "severity": severity,
        "message": (
            f"说明5项级科目名称与T5不一致（{code}）：表格“{expected_name}”，"
            f"说明“{actual_name}”（应以表格“{expected_name}”为准）"
        ),
        "page": page,
        "evidence_status": evidence_status,
        "location": {
            "page": page,
            "code": code,
            "row": code,
            "actual_name": actual_name,
            "expected_name": expected_name,
            "table_refs": [{"role": "说明原文", "page": page}],
        },
        "tags": ["T5", "V33-227"],
    }


# ---------------------------------------------------------------------------
# Bug 1: aggregate 去重同一物理 missing_operand 的无身份副本与带 relation 副本
# ---------------------------------------------------------------------------


def test_aggregate_dedups_unstamped_and_stamped_missing_operands_regardless_of_order():
    unstamped = {
        "source": "text",
        "section": "支出决算具体情况说明",
        "role": "科目名称对应关系",
        "page": 19,
        "row_key": "机关事业单位基本职业年金缴费支出",
        "column": "决算金额",
        "stage": "决算",
        "year": 2023,
    }
    stamped = {
        **unstamped,
        "check_relation_id": "V33-222:general:text:item:6447",
        "sub_check_id": "general:text:item:6447",
        "rule_id": "V33-222",
    }

    for entries in (
        [{"reason_codes": ["name_mismatch_confirmed"], "missing_operands": [unstamped]},
         {"reason_codes": ["name_mismatch_confirmed"], "missing_operands": [stamped]}],
        [{"reason_codes": ["name_mismatch_confirmed"], "missing_operands": [stamped]},
         {"reason_codes": ["name_mismatch_confirmed"], "missing_operands": [unstamped]}],
    ):
        agg = aggregate(entries)
        assert len(agg["missing_operands"]) == 1
        assert agg["missing_operands"][0]["check_relation_id"] == "V33-222:general:text:item:6447"
        assert agg["missing_operands"][0]["rule_id"] == "V33-222"


def test_aggregate_preserves_distinct_check_relation_identities():
    first = {
        "source": "text",
        "section": "支出决算具体情况说明",
        "role": "科目名称对应关系",
        "page": 19,
        "row_key": "机关事业单位基本职业年金缴费支出",
        "column": "决算金额",
        "check_relation_id": "V33-222:general:text:item:6447",
        "sub_check_id": "general:text:item:6447",
        "rule_id": "V33-222",
    }
    second = {
        **first,
        "row_key": "机关事业其他行政事业单位养老支出",
        "check_relation_id": "V33-222:general:text:item:6559",
        "sub_check_id": "general:text:item:6559",
    }
    agg = aggregate([
        {"reason_codes": ["name_mismatch_confirmed"], "missing_operands": [first, second]},
    ])
    assert len(agg["missing_operands"]) == 2
    assert {item["row_key"] for item in agg["missing_operands"]} == {
        "机关事业单位基本职业年金缴费支出",
        "机关事业其他行政事业单位养老支出",
    }


# ---------------------------------------------------------------------------
# Bug 2: 双向 near 候选匹配（含中插字、前插字，同时守住短名称误匹配边界）
# ---------------------------------------------------------------------------


def test_near_subject_name_candidate_handles_prefix_and_middle_insertions():
    assert _is_near_subject_name_candidate(
        "机关事业单位基本职业年金缴费支出",
        "机关事业单位职业年金缴费支出",
    )
    assert _is_near_subject_name_candidate(
        "机关事业其他行政事业单位养老支出",
        "其他行政事业单位养老支出",
    )
    assert _is_near_subject_name_candidate(
        "其他城乡社区支出",
        "其他城乡社区公共设施支出",
    )
    # 守住 4 字短名称与无关科目不误匹配
    assert not _is_near_subject_name_candidate("行政事业运行", "行政运行")
    assert not _is_near_subject_name_candidate("不存在的专项补助支出", "行政运行")


def test_hierarchy_fact_classifies_longer_narrative_names_as_name_mismatch_confirmed():
    facts = [
        _t5_fact("208", "社会保障和就业支出", "3000000"),
        _t5_fact("20805", "行政事业单位养老支出", "3000000"),
        _t5_fact("2080506", "机关事业单位职业年金缴费支出", "1000000"),
        _t5_fact("2080599", "其他行政事业单位养老支出", "2000000"),
    ]
    page_text = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1、社会保障和就业支出(类)行政事业单位养老支出(款)机关事业单位基本职业年金缴费支出(项)决算为100.00万元。\n"
        "2、社会保障和就业支出(类)行政事业单位养老支出(款)机关事业其他行政事业单位养老支出(项)决算为200.00万元。\n"
        "3、社会保障和就业支出(类)行政事业单位养老支出(款)完全不存在的专项支出(项)决算为10.00万元。\n"
    )
    doc = build_document("fake.pdf", [page_text], [[]], 1024)
    doc.fact_context = FactContext(facts=facts, org_name="测试单位", fiscal_year=2025)

    pat = re.compile(
        r"(?P<klass>[^\d，,。；;：:、.()“”\n]+)\(类\)"
        r"(?P<kuan>[^\d，,。；;：:、.()“”\n]+)\(款\)"
        r"(?P<xiang>[^\d，,。；;：:、.()“”\n]+)\(项\)"
    )
    matches = list(pat.finditer(page_text))
    assert len(matches) == 3

    fact0, reason0 = _hierarchy_fact(doc, facts, matches[0])
    fact1, reason1 = _hierarchy_fact(doc, facts, matches[1])
    fact2, reason2 = _hierarchy_fact(doc, facts, matches[2])

    assert fact0 is None and "编码2080506" in reason0
    assert fact1 is None and "编码2080599" in reason1
    assert fact2 is None and "在表内找不到同名科目" in reason2

    # 前两条是 name_mismatch_confirmed，第三条真实缺失仍为 text_binding_failed
    agg_mismatch = aggregate(diagnostics_for_texts(doc, [reason0, reason1], rule_id="V33-222"))
    assert agg_mismatch["reason_codes"] == ["name_mismatch_confirmed"]
    assert {op["row_key"] for op in agg_mismatch["missing_operands"]} == {
        "机关事业单位基本职业年金缴费支出",
        "机关事业其他行政事业单位养老支出",
    }
    agg_missing = aggregate(diagnostics_for_texts(doc, [reason2], rule_id="V33-222"))
    assert "text_binding_failed" in agg_missing["reason_codes"]
    assert {op["row_key"] for op in agg_missing["missing_operands"]} == {"完全不存在的专项支出"}
    agg_mixed = aggregate(diagnostics_for_texts(doc, [reason0, reason2], rule_id="V33-222"))
    assert set(agg_mixed["reason_codes"]) == {"name_mismatch_confirmed", "text_binding_failed"}


# ---------------------------------------------------------------------------
# Bug 3: V33-227 同款项级前插字容错识别
# ---------------------------------------------------------------------------


def test_tolerant_functional_item_codes_matches_prefix_insertion_within_same_section():
    table_entries = {
        "2080506": {
            "level": "项",
            "section_name": "行政事业单位养老支出",
            "name": "机关事业单位职业年金缴费支出",
        },
        "2080599": {
            "level": "项",
            "section_name": "行政事业单位养老支出",
            "name": "其他行政事业单位养老支出",
        },
        "2120399": {
            "level": "项",
            "section_name": "城乡社区公共设施",
            "name": "其他城乡社区公共设施支出",
        },
    }
    assert _tolerant_functional_item_codes(
        table_entries, "行政事业单位养老支出", "机关事业单位基本职业年金缴费支出"
    ) == ["2080506"]
    assert _tolerant_functional_item_codes(
        table_entries, "行政事业单位养老支出", "机关事业其他行政事业单位养老支出"
    ) == ["2080599"]
    assert _tolerant_functional_item_codes(
        table_entries, "城乡社区公共设施", "其他城乡社区支出"
    ) == ["2120399"]
    # 不得把真实缺失科目或跨款无关短词错绑进来
    assert _tolerant_functional_item_codes(
        table_entries, "行政事业单位养老支出", "完全不存在的专项支出"
    ) == []


# ---------------------------------------------------------------------------
# Plan A + 零漏报（Fail-Closed）安全边界测试
# ---------------------------------------------------------------------------


def test_plan_a_cascades_single_and_multi_clause_v33227_including_dedup_truncated_detail():
    # 场景 1（规划局样本特征）：单条 name_mismatch_confirmed + 单条可见正式 V33-227
    ob_single = CoverageObligationItem(
        obligation_id="OBL-TXT-T5",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        status="insufficient_data",
        blocks_gate=True,
        checkers=["V33-222", "V33-227", "V33-251"],
        reason_codes=["name_mismatch_confirmed"],
        next_action="human_confirm",
        missing_operands=[
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 25,
                "row_key": "其他城乡社区支出",
                "column": "决算金额",
                "check_relation_id": "V33-222:general:text:item:11480",
            }
        ],
        detail=(
            "科目名称差异候选：正文第25页写“其他城乡社区支出”；同款表格第16页编码2120399写“其他城乡社区公共设施支出”。"
            "名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。；结构：名称对应位置 第25页 text:支出决算具体情况说明"
        ),
    )
    payload_single = {
        "issues": {
            "all": [
                _v33227_issue(
                    "V33-227-7",
                    code="2120399",
                    actual_name="其他城乡社区支出",
                    expected_name="其他城乡社区公共设施支出",
                    page=25,
                )
            ]
        }
    }
    # U-7 合同保持不变：V33-227（非金额发现）仍不把金额核对标记为“已被金额问题覆盖”
    assert obligation_covered_by_finding(ob_single, payload_single, visible_issue_ids={"V33-227-7"}) is False
    assert cascade_root_issue_ids(ob_single, payload_single, visible_issue_ids={"V33-227-7"}) == ["V33-227-7"]
    assert cascade_root_issue_id(ob_single, payload_single, visible_issue_ids={"V33-227-7"}) == "V33-227-7"
    assert blocking_coverage_obligations([ob_single], payload_single, visible_issue_ids={"V33-227-7"}) == []
    assert [
        item.obligation_id
        for item in cascaded_coverage_obligations([ob_single], payload_single, visible_issue_ids={"V33-227-7"})["V33-227-7"]
    ] == ["OBL-TXT-T5"]

    # 场景 2（科创办样本特征）：两条缺数 + 第二句因 check_obligations 按分号去重省略末尾句后，仍挂入两条 V33-227 下方
    ob_multi = CoverageObligationItem(
        obligation_id="OBL-TXT-T5",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        status="insufficient_data",
        blocks_gate=True,
        checkers=["V33-222", "V33-227", "V33-251"],
        reason_codes=["name_mismatch_confirmed"],
        next_action="human_confirm",
        missing_operands=[
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 19,
                "row_key": "机关事业单位基本职业年金缴费支出",
                "column": "决算金额",
                "check_relation_id": "V33-222:general:text:item:6447",
            },
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 19,
                "row_key": "机关事业其他行政事业单位养老支出",
                "column": "决算金额",
                "check_relation_id": "V33-222:general:text:item:6559",
            },
        ],
        detail=(
            "科目名称差异候选：正文第19页写“机关事业单位基本职业年金缴费支出”；"
            "同款表格第12页编码2080506写“机关事业单位职业年金缴费支出”。"
            "名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。；"
            "科目名称差异候选：正文第19页写“机关事业其他行政事业单位养老支出”；"
            "同款表格第12页编码2080599写“其他行政事业单位养老支出”。"
            "名称不一致，尚未绑定金额；结构：名称对应位置 第19页 text:支出决算具体情况说明"
        ),
    )
    issue_25 = _v33227_issue(
        "V33-227-25",
        code="2080506",
        actual_name="机关事业单位基本职业年金缴费支出",
        expected_name="机关事业单位职业年金缴费支出",
        page=19,
    )
    issue_26 = _v33227_issue(
        "V33-227-26",
        code="2080599",
        actual_name="机关事业其他行政事业单位养老支出",
        expected_name="其他行政事业单位养老支出",
        page=19,
    )
    payload_multi = {"issues": {"all": [issue_25, issue_26]}}
    visible = {"V33-227-25", "V33-227-26"}

    assert cascade_root_issue_ids(ob_multi, payload_multi, visible_issue_ids=visible) == [
        "V33-227-25",
        "V33-227-26",
    ]
    assert blocking_coverage_obligations([ob_multi], payload_multi, visible_issue_ids=visible) == []
    cascaded_multi = cascaded_coverage_obligations([ob_multi], payload_multi, visible_issue_ids=visible)
    assert [x.obligation_id for x in cascaded_multi["V33-227-25"]] == ["OBL-TXT-T5"]
    assert [x.obligation_id for x in cascaded_multi["V33-227-26"]] == ["OBL-TXT-T5"]


def test_plan_a_fail_closed_preserves_blocking_when_any_real_gap_or_suppression_exists():
    base_kwargs = dict(
        obligation_id="OBL-TXT-T5",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        status="insufficient_data",
        blocks_gate=True,
        checkers=["V33-222", "V33-227", "V33-251"],
        next_action="human_confirm",
        missing_operands=[
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 19,
                "row_key": "机关事业单位基本职业年金缴费支出",
                "column": "决算金额",
                "check_relation_id": "V33-222:general:text:item:6447",
            },
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 19,
                "row_key": "机关事业其他行政事业单位养老支出",
                "column": "决算金额",
                "check_relation_id": "V33-222:general:text:item:6559",
            },
        ],
        detail=(
            "科目名称差异候选：正文第19页写“机关事业单位基本职业年金缴费支出”；"
            "同款表格第12页编码2080506写“机关事业单位职业年金缴费支出”。"
            "名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。；"
            "科目名称差异候选：正文第19页写“机关事业其他行政事业单位养老支出”；"
            "同款表格第12页编码2080599写“其他行政事业单位养老支出”。名称不一致，尚未绑定金额"
        ),
    )
    issue_25 = _v33227_issue(
        "V33-227-25",
        code="2080506",
        actual_name="机关事业单位基本职业年金缴费支出",
        expected_name="机关事业单位职业年金缴费支出",
        page=19,
    )
    issue_26 = _v33227_issue(
        "V33-227-26",
        code="2080599",
        actual_name="机关事业其他行政事业单位养老支出",
        expected_name="其他行政事业单位养老支出",
        page=19,
    )
    payload = {"issues": {"all": [issue_25, issue_26]}}
    visible = {"V33-227-25", "V33-227-26"}

    # 1. 混入真实缺失/找不到科目 reason_code（如 text_binding_failed）时，绝不隐藏 OBL-TXT-T5
    ob_mixed_code = CoverageObligationItem(
        **{**base_kwargs, "reason_codes": ["name_mismatch_confirmed", "text_binding_failed"]}
    )
    assert blocking_coverage_obligations([ob_mixed_code], payload, visible_issue_ids=visible) == [ob_mixed_code]

    # 2. detail 中除了名称差异外还有真实缺漏原因文字时，绝不隐藏
    ob_mixed_detail = CoverageObligationItem(
        **{
            **base_kwargs,
            "reason_codes": ["name_mismatch_confirmed"],
            "detail": base_kwargs["detail"] + "；说明中的项“真实缺失项”在表内找不到同名科目；结构：名称对应位置 第19页",
        }
    )
    assert blocking_coverage_obligations([ob_mixed_detail], payload, visible_issue_ids=visible) == [ob_mixed_detail]

    # 3. 两处差错只有一处报出了正式 V33-227（另一处漏报或被禁用/降级），绝不隐藏
    ob_pure = CoverageObligationItem(**{**base_kwargs, "reason_codes": ["name_mismatch_confirmed"]})
    assert blocking_coverage_obligations(
        [ob_pure], {"issues": {"all": [issue_25]}}, visible_issue_ids={"V33-227-25"}
    ) == [ob_pure]
    assert blocking_coverage_obligations(
        [ob_pure], payload, visible_issue_ids={"V33-227-25"}  # V33-227-26 被忽略/不可见
    ) == [ob_pure]

    issue_26_info = _v33227_issue(
        "V33-227-26",
        code="2080599",
        actual_name="机关事业其他行政事业单位养老支出",
        expected_name="其他行政事业单位养老支出",
        page=19,
        severity="info",
    )
    assert blocking_coverage_obligations(
        [ob_pure], {"issues": {"all": [issue_25, issue_26_info]}}, visible_issue_ids=visible
    ) == [ob_pure]

    issue_26_degraded = _v33227_issue(
        "V33-227-26",
        code="2080599",
        actual_name="机关事业其他行政事业单位养老支出",
        expected_name="其他行政事业单位养老支出",
        page=19,
        evidence_status="degraded_missing_evidence",
    )
    assert blocking_coverage_obligations(
        [ob_pure], {"issues": {"all": [issue_25, issue_26_degraded]}}, visible_issue_ids=visible
    ) == [ob_pure]

    # 4. 存在上游阻断 blocked_by 或缺少 missing_operands 时，绝不隐藏
    ob_blocked = CoverageObligationItem(
        **{**base_kwargs, "reason_codes": ["name_mismatch_confirmed"], "blocked_by": [{"ref": "OBL-STRUCT-1", "reason_code": "table_missing"}]}
    )
    assert blocking_coverage_obligations([ob_blocked], payload, visible_issue_ids=visible) == [ob_blocked]

    ob_no_operands = CoverageObligationItem(
        **{**base_kwargs, "reason_codes": ["name_mismatch_confirmed"], "missing_operands": []}
    )
    assert blocking_coverage_obligations([ob_no_operands], payload, visible_issue_ids=visible) == [ob_no_operands]

    # 5. 非 OBL-TXT-T5 义务不误走该规则
    ob_other = CoverageObligationItem(
        **{**base_kwargs, "obligation_id": "OBL-TXT-T6", "reason_codes": ["name_mismatch_confirmed"]}
    )
    assert cascade_root_issue_ids(ob_other, payload, visible_issue_ids=visible) == []


def test_sub_check_lookup_isolation_prevents_duplicate_operands():
    """同错误文案登记在不同 sub_check_id 下时，各回执只取回自己的缺数操作数，绝不交叉污染。"""
    doc = build_document("fake.pdf", ["第十七页", "第十八页"], [[[]], [[]]], 1024)
    shared_reason = "说明中的款“医疗保障”在表内找不到同名科目，无法与表格金额对应"
    record_gap(
        doc,
        shared_reason,
        reason_codes=["text_binding_failed"],
        missing_operands=[
            text_missing_operand(
                section="支出决算具体情况说明",
                role="科目名称对应关系",
                page=17,
                row_key="事业单位医疗",
                column="决算金额",
                stage="决算",
                year=2025,
            )
        ],
        sub_check_id="general:text:item:7286",
    )
    record_gap(
        doc,
        shared_reason,
        reason_codes=["text_binding_failed"],
        missing_operands=[
            text_missing_operand(
                section="支出决算具体情况说明",
                role="科目名称对应关系",
                page=18,
                row_key="其他卫生健康",
                column="决算金额",
                stage="决算",
                year=2025,
            )
        ],
        sub_check_id="general:text:item:7405",
    )

    f1 = fields_for_text(doc, shared_reason, rule_id="V33-222", sub_check_id="general:text:item:7286")
    f2 = fields_for_text(doc, shared_reason, rule_id="V33-222", sub_check_id="general:text:item:7405")
    assert len(f1["missing_operands"]) == 1
    assert f1["missing_operands"][0]["row_key"] == "事业单位医疗"
    assert len(f2["missing_operands"]) == 1
    assert f2["missing_operands"][0]["row_key"] == "其他卫生健康"

    agg = aggregate([f1, f2], obligation_id="OBL-TXT-T5")
    assert agg is not None
    keys = [(op["page"], op["row_key"]) for op in agg["missing_operands"]]
    assert keys == [(17, "事业单位医疗"), (18, "其他卫生健康")]


def test_prefix_and_suffix_omission_detected_by_v33_227_and_matched_by_v33_222():
    """同款下同时省略前缀“机关”和后缀“缴费支出”时，V33-227 必须正式检出且 V33-222 归为名称差异。"""
    t5_rows = [
        ["功能分类科目编码", "科目名称", "本年支出", "基本支出", "项目支出"],
        ["208", "社会保障和就业支出", "300.00", "300.00", "0.00"],
        ["20805", "行政事业单位养老支出", "300.00", "300.00", "0.00"],
        ["2080505", "机关事业单位基本养老保险缴费支出", "200.00", "200.00", "0.00"],
        ["2080506", "机关事业单位职业年金缴费支出", "100.00", "100.00", "0.00"],
        ["合计", "", "300.00", "300.00", "0.00"],
    ]
    p1 = "一般公共预算财政拨款支出决算表\n208 社会保障和就业支出\n20805 行政事业单位养老支出\n2080505 机关事业单位基本养老保险缴费支出 200.00\n2080506 机关事业单位职业年金缴费支出 100.00"
    p2 = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1.社会保障和就业支出（类）行政事业单位养老支出（款）事业单位基本养老保险（项）。年初预算为200万元，支出决算为200万元。\n"
        "2.社会保障和就业支出（类）行政事业单位养老支出（款）事业单位职业年金（项）。年初预算为100万元，支出决算为100万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("fake.pdf", [p1, p2], [[t5_rows], [[]]], 2048)
    facts = [
        _t5_fact("208", "社会保障和就业支出", "3000000", page=1),
        _t5_fact("20805", "行政事业单位养老支出", "3000000", page=1),
        _t5_fact("2080505", "机关事业单位基本养老保险缴费支出", "2000000", page=1),
        _t5_fact("2080506", "机关事业单位职业年金缴费支出", "1000000", page=1),
    ]
    doc.fact_context = FactContext(facts=facts, org_name="测试单位", fiscal_year=2025)

    assert _is_near_subject_name_candidate("事业单位基本养老保险", "机关事业单位基本养老保险缴费")
    assert _is_near_subject_name_candidate("事业单位职业年金", "机关事业单位职业年金缴费")
    # 守住 4 字短名不被误放行
    assert not _is_near_subject_name_candidate("行政运行", "机关行政运行缴费")

    table_entries = {
        "2080505": {"level": "项", "section_name": "行政事业单位养老支出", "name": "机关事业单位基本养老保险缴费支出"},
        "2080506": {"level": "项", "section_name": "行政事业单位养老支出", "name": "机关事业单位职业年金缴费支出"},
    }
    assert _tolerant_functional_item_codes(table_entries, "行政事业单位养老支出", "事业单位基本养老保险") == ["2080505"]
    assert _tolerant_functional_item_codes(table_entries, "行政事业单位养老支出", "事业单位职业年金") == ["2080506"]

    v227_issues = R33227_Narrative5_T5_NameConsistency().apply(doc)
    by_code = {issue.location.get("code"): issue for issue in v227_issues}
    assert "2080505" in by_code and by_code["2080505"].severity == "warn"
    assert by_code["2080505"].location["actual_name"] == "事业单位基本养老保险"
    assert "2080506" in by_code and by_code["2080506"].severity == "warn"
    assert by_code["2080506"].location["actual_name"] == "事业单位职业年金"

    pat = re.compile(
        r"(?P<klass>[^\d，,。；;：:、.()（）“”\n]+)[(（]类[)）]"
        r"(?P<kuan>[^\d，,。；;：:、.()（）“”\n]+)[(（]款[)）]"
        r"(?P<xiang>[^\d，,。；;：:、.()（）“”\n]+)[(（]项[)）]"
    )
    matches = list(pat.finditer(p2))
    assert len(matches) == 2
    f0, r0 = _hierarchy_fact(doc, facts, matches[0], sub_check_id="item:0")
    f1, r1 = _hierarchy_fact(doc, facts, matches[1], sub_check_id="item:1")
    assert f0 is None and "编码2080505" in r0
    assert f1 is None and "编码2080506" in r1
    diag = aggregate(diagnostics_for_texts(doc, [r0, r1], rule_id="V33-222"))
    assert diag["reason_codes"] == ["name_mismatch_confirmed"]
    assert {op["row_key"] for op in diag["missing_operands"]} == {"事业单位基本养老保险", "事业单位职业年金"}


def test_wrong_kuan_candidate_keeps_unbound_and_cascades_only_when_all_levels_reported():
    """同款下无法独立双项互证时保持未绑定，且仅当 V33-227 报全所有款/项差异才挂入下方。"""
    facts = [
        _t5_fact("210", "卫生健康支出", "3000000", page=10),
        _t5_fact("21011", "行政事业单位医疗", "1000000", page=10),
        _t5_fact("2101102", "事业单位医疗", "1000000", page=10),
        _t5_fact("21099", "其他卫生健康支出", "2000000", page=10),
        _t5_fact("2109999", "其他卫生健康支出", "2000000", page=10),
    ]
    p17 = "五、一般公共预算财政拨款支出决算情况说明\n7.卫生健康支出（类）医疗保障（款）事业单位医疗（项）。支出决算为100.00万元。"
    p18 = "8.卫生健康支出（类）医疗保障（款）其他卫生健康（项）。支出决算为200.00万元。"
    doc = build_document("fake.pdf", [""] * 16 + [p17, p18], [[] for _ in range(18)], 1024)
    doc.fact_context = FactContext(facts=facts, org_name="测试单位", fiscal_year=2025)

    from src.engine.native_final_disclosure import _ITEM
    from src.engine.native_narrative_checks import _Text

    text_obj = _Text.from_pages(doc.page_texts)
    items = list(_ITEM.finditer(text_obj.compact))
    assert len(items) == 2

    f7, r7 = _hierarchy_fact(doc, facts, items[0], siblings=items, sub_check_id="general:text:item:7")
    f8, r8 = _hierarchy_fact(doc, facts, items[1], siblings=items, sub_check_id="general:text:item:8")
    # 绝不静默绑定金额（尊重双项互证约束）
    assert f7 is None and "编码21011" in r7 and "涉及项“事业单位医疗”" in r7
    assert f8 is None and "编码21099" in r8 and "涉及项“其他卫生健康”" in r8 and "编码2109999" in r8

    d7 = fields_for_text(doc, r7, rule_id="V33-222", sub_check_id="general:text:item:7")
    d8 = fields_for_text(doc, r8, rule_id="V33-222", sub_check_id="general:text:item:8")
    assert d7["reason_codes"] == ["name_mismatch_confirmed"]
    assert d8["reason_codes"] == ["name_mismatch_confirmed"]
    assert len(d7["missing_operands"]) == 1 and d7["missing_operands"][0]["row_key"] == "事业单位医疗"
    assert len(d8["missing_operands"]) == 1 and d8["missing_operands"][0]["row_key"] == "其他卫生健康"

    agg = aggregate([d7, d8], obligation_id="OBL-TXT-T5")
    assert [(op["page"], op["row_key"]) for op in agg["missing_operands"]] == [
        (17, "事业单位医疗"),
        (18, "其他卫生健康"),
    ]

    issue_kuan1 = _v33227_issue("V33-227-20", code="21011", actual_name="医疗保障", expected_name="行政事业单位医疗", page=17)
    issue_kuan2 = _v33227_issue("V33-227-21", code="21099", actual_name="医疗保障", expected_name="其他卫生健康支出", page=18)
    issue_xiang2 = _v33227_issue("V33-227-22", code="2109999", actual_name="其他卫生健康", expected_name="其他卫生健康支出", page=18)

    ob_t5 = CoverageObligationItem(
        obligation_id="OBL-TXT-T5",
        group_id="txt",
        group_title="表文一致性",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        status="insufficient_data",
        reason="insufficient_data",
        blocks_gate=True,
        checkers=["V33-222", "V33-227"],
        depends_on=["table:FIN_05"],
        reason_codes=agg["reason_codes"],
        missing_operands=agg["missing_operands"],
        detail=f"{r7}；{r8}；结构：名称对应位置 第17页 text:支出决算具体情况说明",
    )

    all_issues = [issue_kuan1, issue_kuan2, issue_xiang2]
    vis = {"V33-227-20", "V33-227-21", "V33-227-22"}
    assert blocking_coverage_obligations([ob_t5], {"issues": {"all": all_issues}}, visible_issue_ids=vis) == []
    roots = cascade_root_issue_ids(ob_t5, {"issues": {"all": all_issues}}, visible_issue_ids=vis)
    assert set(roots) == vis

    # 负控：缺任一条 V33-227（漏报 2109999 或漏报 21099）必须 fail-closed 保留 OBL-TXT-T5
    assert blocking_coverage_obligations(
        [ob_t5], {"issues": {"all": [issue_kuan1, issue_kuan2]}}, visible_issue_ids={"V33-227-20", "V33-227-21"}
    ) == [ob_t5]
    assert blocking_coverage_obligations(
        [ob_t5], {"issues": {"all": [issue_kuan1, issue_xiang2]}}, visible_issue_ids={"V33-227-20", "V33-227-22"}
    ) == [ob_t5]


def test_quality_gate_aligns_with_cascaded_t5_name_mismatch_and_fails_closed():
    """商务委场景：OBL-TXT-T5/V33-222 仅因已正式报出的 name_mismatch_confirmed 连带未完成时，不再误挂 review_required。"""
    good_pages = {
        "page_count": 10,
        "text_page_count": 10,
        "low_text_pages": [],
        "low_text_page_count": 0,
        "scanned_pages": [],
        "scanned_page_count": 0,
        "page_coverage": 1.0,
    }
    issue_25 = _v33227_issue(
        "V33-227-25",
        code="2080501",
        actual_name="归口管理的行政单位离退休",
        expected_name="行政单位离退休",
        page=25,
    )
    issue_25["evidence_status"] = "complete"
    result_row = {"job_id": "job-sw", "issues": {"all": [issue_25]}}

    ob_t5_dict = {
        "obligation_id": "OBL-TXT-T5",
        "group_id": "txt",
        "group_title": "表文一致性",
        "title": "一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "checkers": ["V33-222", "V33-227"],
        "depends_on": ["table:FIN_05"],
        "blocks_gate": True,
        "reason_codes": ["name_mismatch_confirmed"],
        "blocked_by": [],
        "missing_operands": [
            {
                "source": "text",
                "section": "支出决算具体情况说明",
                "role": "科目名称对应关系",
                "page": 25,
                "row_key": "归口管理的行政单位离退休",
                "column": "决算金额",
                "stage": "决算",
                "year": 2025,
            }
        ],
        "detail": (
            "科目名称差异候选：正文第25页写“归口管理的行政单位离退休”；"
            "同款表格第17页编码2080501写“行政单位离退休”。名称不一致，尚未绑定金额；"
            "请核对科目名称后确认，不能仅凭金额相同认定一致。；结构：名称对应位置 第25页 text:支出决算具体情况说明"
        ),
    }
    coverage = {
        "catalog_version": "test",
        "applicable_total": 1,
        "completed_total": 0,
        "not_applicable_total": 0,
        "unresolved_total": 1,
        "blocking_total": 1,
        "by_reason": {"insufficient_data": 1},
        "by_reason_labels": {"insufficient_data": "取数不足"},
        "blocking_obligation_ids": ["OBL-TXT-T5"],
        "instances": [ob_t5_dict],
    }
    rule_summary = {
        "total_rules": 10,
        "executed": 9,
        "pass": 8,
        "fail": 1,
        "not_applicable": 0,
        "insufficient_data": 1,
        "parse_error": 0,
        "execution_error": 0,
        "unresolved_total": 1,
        "unresolved_rules": [
            {
                "rule_id": "V33-222",
                "status": "insufficient_data",
                "detail": "科目名称差异候选：正文第25页写“归口管理的行政单位离退休”；同款表格第17页编码2080501写“行政单位离退休”。名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。",
                "diagnostics": [{"reason_codes": ["name_mismatch_confirmed"], "blocked_by": []}],
            }
        ],
    }

    gate = _evaluate_quality_gate(
        page_assessment=good_pages,
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=1,
        rule_execution_summary=rule_summary,
        doc_type="dept_final",
        obligation_coverage=coverage,
        result_row=result_row,
    )
    assert gate["status"] == "done"
    assert gate["quality_status"] == "complete"
    assert gate["analysis_conclusion"] == "findings_detected"
    assert gate["review_reasons"] == []

    # 负控 1：若正式 V33-227 缺失（未形成可见根问题），必须保留 review_required + 两个原因码
    gate_no_root = _evaluate_quality_gate(
        page_assessment=good_pages,
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=0,
        rule_execution_summary=rule_summary,
        doc_type="dept_final",
        obligation_coverage=coverage,
        result_row={"job_id": "job-sw", "issues": {"all": []}},
    )
    assert gate_no_root["status"] == "review_required"
    assert {r["code"] for r in gate_no_root["review_reasons"]} >= {
        "rules_insufficient_data",
        "check_obligations_incomplete",
    }

    # 负控 2：若混入其他真实取数不足规则（如 V33-115），仍必须保持 review_required 且只过滤已覆盖的 V33-222
    mixed_summary = {
        **rule_summary,
        "insufficient_data": 2,
        "unresolved_total": 2,
        "unresolved_rules": [
            *rule_summary["unresolved_rules"],
            {
                "rule_id": "V33-115",
                "status": "insufficient_data",
                "detail": "表缺失：收入支出决算总表",
                "diagnostics": [{"reason_codes": ["table_missing"], "blocked_by": [{"dimension": "table", "ref": "T1", "reason_code": "table_missing"}]}],
            },
        ],
    }
    gate_mixed = _evaluate_quality_gate(
        page_assessment=good_pages,
        report_kind="final",
        report_year=2025,
        ai_requested=False,
        ai_degraded=False,
        issue_total=1,
        rule_execution_summary=mixed_summary,
        doc_type="dept_final",
        obligation_coverage=coverage,
        result_row=result_row,
    )
    assert gate_mixed["status"] == "review_required"
    insufficient_reason = next(r for r in gate_mixed["review_reasons"] if r["code"] == "rules_insufficient_data")
    assert [u["rule_id"] for u in insufficient_reason["unresolved_rules"]] == ["V33-115"]
