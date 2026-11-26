from src.services.unfinished_check_groups import build_unfinished_check_groups


CODE = "FIN_08_gov_fund"
TABLE = "政府性基金预算财政拨款收入支出决算表"


def _table(id_, page, box=None):
    return {
        "id": id_, "record_type": "structured_review", "table_code": CODE, "page_number": page,
        "reason": "amount_column_unmapped", "cells": [{"page": page, "bbox": box}],
    }


def test_group_column_checks_by_source_and_leave_unrelated_checks_separate():
    checks = [
        _table("mapping", 24, [10, 10, 20, 20]),
        {
            "id": "internal", "title": "合计核对",
            "blocked_by": [{"ref": f"第24页{CODE}", "reason_code": "table_mapping_incomplete"}],
        },
        {
            "id": "narrative", "title": "表文核对",
            "detail": f"《{TABLE}》「本年支出」列段内未找到「合计」子表头",
            "reason_codes": ["column_identity_unconfirmed"],
            "missing_operands": [{"page": 24, "table_code": CODE, "row_key": "total"}],
        },
        {
            "id": "cross", "title": "表间核对",
            "detail": f"{TABLE}本年支出合计未取到",
            "reason_codes": ["table_mapping_incomplete"],
        },
        {
            "id": "other", "title": "某科目核对",
            "detail": "支出决算表科目210的科目明细不完整",
        },
    ]
    first, other = build_unfinished_check_groups(checks, {CODE: TABLE, "FIN_03_expenditure": "支出决算表"})
    assert first["check_ids"] == ["mapping", "internal", "narrative", "cross"]
    assert first["title"] == "本年支出子列未确认"
    assert first["evidence"] == [{"page": 24, "bbox": [10, 10, 20, 20]}]
    assert other["location_level"] == "unlocated"
    assert other["table_name"] == "支出决算表"
    assert other["subject"] == "210"


def test_a_table_only_reference_cannot_choose_between_two_regions_of_the_same_table():
    checks = [
        _table("mapping-a", 24, [10, 10, 20, 20]),
        _table("mapping-b", 24, [30, 30, 40, 40]),
        {
            "id": "unknown-region", "title": "支出列核对",
            "reason_codes": ["column_identity_unconfirmed"],
            "missing_operands": [{"page": 24, "table_code": CODE, "row_key": "total"}],
        },
    ]
    first, second, at_table = build_unfinished_check_groups(checks, {CODE: TABLE})
    assert [group["check_ids"] for group in (first, second, at_table)] == [
        ["mapping-a"], ["mapping-b"], ["unknown-region"],
    ]
    assert at_table["location_level"] == "table"
    assert at_table["evidence"] == []


def test_p1_fallback_locates_table_subject_and_narrative_and_marks_blocking():
    checks = [
        {
            "id": "OBL-CROSS-SAN-GONG-ECON",
            "record_type": "unfinished_check",
            "title": "三公经费表 与 财政拨款经济分类表 资金来源一致性",
            "reason_codes": ["table_missing"],
            "detail": "未定位到唯一的《财政拨款“三公”经费支出决算表》结构化表",
        },
        {
            "id": "OBL-NARRATIVE-AMOUNT",
            "record_type": "unfinished_check",
            "title": "文字说明中的金额与占比可复算",
            "reason_codes": ["row_level_unresolved"],
            "detail": "评级类别未识别或不是互斥的标准评级，不能据此认定数量闭合",
        },
        {
            "id": "rule:V33-120",
            "record_type": "processing_notice",
            "title": "V33-120 · 部分规则取数不足",
            "detail": "支出决算表科目210/total_actual直接层级明细不完整",
        },
    ]
    reading_tables = [
        {"table_code": "FIN_03_expenditure", "page": 13, "bbox": [10, 10, 500, 700], "title": "支出决算表"},
        {"table_code": "FIN_07_three_public", "page": 23, "bbox": [20, 30, 520, 300], "title": "一般公共预算财政拨款“三公”经费及机关运行经费支出决算表"},
    ]
    page_texts = [""] * 40
    page_texts[12] = "支出决算表\n201 一般公共服务支出 100.00"
    page_texts[13] = "208 社会保障和就业支出 200.00\n210\n卫生健康支出\n5185.32"
    page_texts[36] = "绩效自评项目 132 个（其中，绩效评级为“优”的项目 55 个；绩效评级为“良”的项目 76 个）"

    groups = build_unfinished_check_groups(
        checks,
        {
            "FIN_03_expenditure": "支出决算表",
            "FIN_07_three_public": "财政拨款“三公”经费支出决算表",
        },
        reading_tables=reading_tables,
        page_texts=page_texts,
        blocking_obligation_ids=["OBL-CROSS-SAN-GONG-ECON", "OBL-NARRATIVE-AMOUNT"],
    )
    by_title = {g["title"]: g for g in groups}
    assert by_title["目标表待定位"]["page"] == 23
    assert by_title["目标表待定位"]["location_level"] == "table"
    assert by_title["目标表待定位"]["evidence"] == [{"page": 23, "bbox": [20, 30, 520, 300]}]
    assert by_title["目标表待定位"]["blocks_completion"] is True

    assert by_title["绩效评级类别待确认"]["page"] == 37
    assert by_title["绩效评级类别待确认"]["location_level"] == "page"
    assert by_title["绩效评级类别待确认"]["blocks_completion"] is True

    assert by_title["科目明细待核对"]["page"] == 14
    assert by_title["科目明细待核对"]["location_level"] == "page"
    assert by_title["科目明细待核对"]["blocks_completion"] is False
    # 阻塞分组排在仅提示分组之前
    assert groups[-1]["title"] == "科目明细待核对"
    assert all(g["plain_reason"] and g["action_hint"] for g in groups)


def test_p0_performance_rating_accepts_you_liang_zhong_cha_and_rejects_mixed():
    from src.engine.native_final_disclosure import check_performance_counts
    from src.engine.rules_v33 import Rule, build_document

    class DummyRule(Rule):
        code, severity = "V33-250", "warn"

        def apply(self, doc):
            return []

    ok_page = (
        "2023年度部门决算\n"
        "三、预算绩效管理工作开展情况\n"
        "绩效自评的2023年度项目132个，涉及预算金额1,983,978.16万元，平均得分90.77分"
        "（其中，绩效评级为“优”的项目55个；绩效评级为“良”的项目76个；绩效评级为“中”的项目1个）。"
    )
    doc_ok = build_document("/tmp/perf_ok.pdf", [ok_page], [[]], 100)
    doc_ok.report_kind = "final"
    doc_ok.dominant_year = 2023
    assert check_performance_counts(DummyRule(), doc_ok) == []

    mixed_page = (
        "2023年度部门决算\n"
        "三、预算绩效管理工作开展情况\n"
        "绩效自评的2023年度项目10个（其中，绩效评级为“优”的项目5个；"
        "绩效评级为“合格”的项目3个；绩效评级为“中”的项目2个）。"
    )
    doc_mixed = build_document("/tmp/perf_mixed.pdf", [mixed_page], [[]], 100)
    doc_mixed.report_kind = "final"
    doc_mixed.dominant_year = 2023
    from src.engine.rule_outcome import RuleDeferred
    import pytest
    with pytest.raises(RuleDeferred):
        check_performance_counts(DummyRule(), doc_mixed)


def test_p0_cmm022_catches_subject_code_class_prefix_mismatch():
    from src.engine.checklist_content_rules import CMM022_SubjectCodeFormat
    from src.engine.rules_v33 import build_document

    page1 = "支出决算表\n201 一般公共服务支出 100.00\n20104 发展与改革事务 100.00"
    page2 = "210 卫生健康支出 5185.32\n20101 卫生健康支出 0.00\n21011 行政事业单位医疗 5181.72"
    table1 = [["201", "一般公共服务支出", "100.00"], ["20104", "发展与改革事务", "100.00"]]
    table2 = [["210", "卫生健康支出", "5185.32"], ["20101", "卫生健康支出", "0.00"], ["21011", "行政事业单位医疗", "5181.72"]]
    doc = build_document("/tmp/cmm022_mismatch.pdf", [page1, page2], [[table1], [table2]], 100)
    issues = CMM022_SubjectCodeFormat().apply(doc)
    assert any("20101" in issue.message and "210" in issue.message for issue in issues)
    assert issues[0].location["page"] == 2


def test_cmm022_locates_second_printed_subject_under_wrong_class():
    from src.engine.checklist_content_rules import CMM022_SubjectCodeFormat
    from src.engine.rules_v33 import build_document

    table = [
        ["212", "城乡社区支出", "7.00"],
        ["21201", "城乡社区管理事务", "7.00"],
        ["220", "自然资源海洋气象等支出", "5.00"],
        ["21201", "城乡社区管理事务", "7.00"],
    ]
    page = "\n".join(" ".join(row) for row in table)
    doc = build_document("/tmp/cmm022_repeat.pdf", [page], [[table]], 100)
    issue, = CMM022_SubjectCodeFormat().apply(doc)
    assert issue.location["pos"] == page.rindex("21201")
    assert "21201 城乡社区管理事务" in issue.evidence_text
    assert page.count(issue.evidence_text) == 1
    assert page.index(issue.evidence_text) > page.index("220")


def test_p0_cross_table_lookup_prefers_table_code_over_nonstandard_title():
    from types import SimpleNamespace
    from src.engine.rules_v33 import _FIN_07_TABLE_ALIASES, _cross_table_by_alias

    table = SimpleNamespace(
        table_code="FIN_07_three_public",
        title="一般公共预算财政拨款“三公”经费及机关运行经费支出决算表",
        page_span=(23, 23),
    )
    doc = SimpleNamespace(parsed_tables={"inst-1": table}, page_texts=[""] * 23)
    assert _cross_table_by_alias(doc, _FIN_07_TABLE_ALIASES, "FIN_07_three_public") is table


def test_covered_by_issue_mode_and_accurate_subject_name_guidance():
    checks = [
        {
            "id": "OBL-TXT-T5",
            "record_type": "unfinished_check",
            "title": "一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
            "detail": "科目名称差异候选（款）：正文第25页写“政府办公厅及相关机构事务”（涉及项“行政运行”）",
            "missing_operands": [
                {"page": 25, "section": "支出决算具体情况说明", "row_key": "行政运行", "role": "科目名称对应关系"},
            ],
        },
        {
            "id": "OBL-TREND-COMPLETION-RATE",
            "record_type": "unfinished_check",
            "title": "预算完成率分母口径与复算",
            "detail": "「事业单位离退休」的本年支出决算存在多个不一致披露",
            "missing_operands": [
                {"page": 26, "section": "预算完成率说明", "row_key": "事业单位离退休", "role": "存在冲突的预算/决算披露"},
            ],
        },
    ]
    groups = build_unfinished_check_groups(
        checks,
        {},
        blocking_obligation_ids=["OBL-TREND-COMPLETION-RATE"],
        covered_obligation_ids=["OBL-TXT-T5"],
    )
    assert [g["handling_mode"] for g in groups] == ["manual_required", "covered_by_issue"]
    assert groups[0]["page"] == 26
    assert groups[0]["location_level"] == "page"
    assert "不一致" in groups[0]["plain_reason"]
    assert groups[1]["page"] == 25
    assert groups[1]["covered_by_issue"] is True
    assert groups[1]["blocks_completion"] is False
    assert "名称不完全一致" in groups[1]["plain_reason"]
    assert "下级明细不完整" not in groups[1]["plain_reason"]


def test_cross_line_char_span_and_numeric_subject_bbox_resolution():
    from src.services.unfinished_check_groups import _find_span_in_compact, _line_union_boxes

    # 模拟“事业运行\n（项）”跨两行
    text = "商业流通事务(款)事业运行(项)。"
    boxes = [
        (100 + i * 10, 610, 110 + i * 10, 625) if i < 13 else (88 + (i - 13) * 10, 640, 98 + (i - 13) * 10, 655)
        for i in range(len(text))
    ]
    span = _find_span_in_compact(text, boxes, {"page": 26, "subject": "事业运行", "title": "事业运行条目显式决算金额"}, [""])
    assert span is not None
    assert text[span[0]:span[1]] == "事业运行(项)"
    rects = _line_union_boxes(boxes, span[0], span[1])
    assert len(rects) == 2
    assert rects[0][1] < 620 and rects[1][1] > 635

    # 模拟上行末尾数字紧邻下行科目编码 210（如 14.68 / 210 卫生健康支出）
    t2 = "14.68210卫生健康支出5185.32"
    b2 = [
        (400 + i * 8, 250, 408 + i * 8, 262) if i < 5 else (70 + (i - 5) * 10, 274, 80 + (i - 5) * 10, 286)
        for i in range(len(t2))
    ]
    span2 = _find_span_in_compact(t2, b2, {"page": 14, "subject": "210", "title": "科目明细待核对"}, [""])
    assert span2 is not None
    assert t2[span2[0]:span2[1]] == "210卫生健康支出"






def test_same_obligation_two_subjects_get_distinct_scopes_and_check_counts():
    """R5：同一义务两处独立阻塞各有稳定范围；影响检查数不含问题卡片。"""
    checks = [
        {
            "id": "OBL-TXT-T5",
            "record_type": "unfinished_check",
            "title": "一般公共预算支出决算表 与 功能分类说明一致",
            "reason_codes": ["operand_missing"],
            "missing_operands": [
                {"page": 26, "code": "2160250", "section": "支出决算具体情况",
                 "row_key": "2160250", "column": "决算金额", "role": "事业运行"},
                {"page": 26, "code": "2160251", "section": "支出决算具体情况",
                 "row_key": "2160251", "column": "决算金额", "role": "其他事业运行"},
            ],
        },
    ]
    findings = [{
        "id": "issue-1", "rule_id": "V33-222",
        "message": "第26页 2160251 表文金额不一致",
        "location": {"page": 26, "table_refs": [{"physical_page": 26, "quote": "2160251"}]},
    }]
    groups = build_unfinished_check_groups(
        checks, {}, findings, blocking_obligation_ids=["OBL-TXT-T5"],
    )
    assert len(groups) == 2
    assert groups[0]["decision_scope"] != groups[1]["decision_scope"]
    assert groups[0]["affected_check_count"] == 1
    assert groups[0]["related_issue_count"] <= 1


def test_scoped_decisions_require_every_blocking_location():
    from types import SimpleNamespace

    from src.services.review_lifecycle_service import (
        obligation_decision_resolved,
        unhandled_coverage_obligations,
    )

    item = SimpleNamespace(obligation_id="OBL-TXT-T5", blocks_gate=True, status="insufficient_data")
    scopes = {"OBL-TXT-T5": ["grp:a", "grp:b"]}
    only_first = {
        "OBL-TXT-T5": {"grp:a": {"decision": "verified_ok"}},
    }
    assert unhandled_coverage_obligations(
        [item], {}, required_scopes=scopes, scoped_decisions=only_first,
    ) == [item]
    both = {
        "OBL-TXT-T5": {
            "grp:a": {"decision": "verified_ok"},
            "grp:b": {"decision": "verified_issue"},
        },
    }
    assert unhandled_coverage_obligations(
        [item], {}, required_scopes=scopes, scoped_decisions=both,
    ) == []
    assert obligation_decision_resolved(
        "OBL-TXT-T5", {}, required_scopes=scopes,
        scoped_decisions={"grp:a": {"decision": "verified_ok"}},
    ) is False
