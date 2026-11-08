"""2026-11-08：认证空白默认按 0（U-5 用户确认）、空白认证按字符中心、占位符汇总、缺表/空表连带项。

背景：建管委 2025 部门决算 32 条“必须处理”中 10 条“未能自动核对”全部因为空白格
保持未知；8 条占位符来自同一页两处“XXXXXXXX”。单位决算另有 9 条核对是“支出决算表
缺失”“三公表为空表”两个已报问题的连带后果。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from src.engine.cell_value_policy import USER_CONFIRMED_BLANK_ZERO, interpret_cell_value
from src.engine.rules_v33 import R33112_PlaceholderCheck, build_document
from src.services.material_detail_query_service import build_coverage
from src.services.obligation_finding_coverage import _table_codes_in, cascade_root_issue_id
from src.services.pdf_parser import PDFParser

IDENTITY = dict(region="上海市普陀区建设和管理委员会", year=2025, table_code="FIN_07_three_public", row=4, column=2)


# ---------------------------------------------------------------------------
# U-5：认证空白默认按 0
# ---------------------------------------------------------------------------


def test_default_convention_treats_only_verified_blank_as_zero(monkeypatch):
    monkeypatch.delenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", raising=False)
    value, presence, basis = interpret_cell_value("", native_blank_verified=True, **IDENTITY)
    assert (value, presence) == (Decimal("0"), "blank")
    assert basis["id"] == USER_CONFIRMED_BLANK_ZERO["id"]
    assert basis["source_kind"] == "user_confirmed"
    # 未经网格认证的空白、横线、“无”“/”等仍保持未知。
    assert interpret_cell_value("", native_blank_verified=False, **IDENTITY)[0] is None
    for raw in ("—", "-", "/", "无", "不适用", "abc"):
        assert interpret_cell_value(raw, native_blank_verified=True, **IDENTITY)[0] is None
    # 任意地区、年度、表种都适用（全系统默认）。
    other = {**IDENTITY, "region": "浙江省某局", "year": 2022, "table_code": "FIN_02_income"}
    assert interpret_cell_value("", native_blank_verified=True, **other)[0] == Decimal("0")


def test_convention_can_be_switched_off_for_controlled_comparisons(monkeypatch):
    for value in ("off", "none", "false", "0"):
        monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", value)
        assert interpret_cell_value("", native_blank_verified=True, **IDENTITY)[0] is None


def test_explicit_convention_file_replaces_builtin(tmp_path, monkeypatch):
    path = tmp_path / "conventions.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", str(path))
    assert interpret_cell_value("", native_blank_verified=True, **IDENTITY)[0] is None


# ---------------------------------------------------------------------------
# 空白认证：字形框蹭进相邻格不算本格墨迹
# ---------------------------------------------------------------------------


def _cell(bbox):
    return SimpleNamespace(raw_text="", numeric_value=None, bbox=bbox, native_blank_verified=None)


def _page(chars):
    return SimpleNamespace(chars=chars, images=[], curves=[], rects=[], lines=[])


def _char(text, x0, top, x1, bottom):
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_glyph_bleeding_from_row_above_does_not_block_blank_certification():
    # 建管委 p20 实测：上一行“4.36”字形框 top=297.7、bottom≈308.7，下一行空白格 top=307.54。
    blank = _cell((466.3, 307.54, 515.05, 323.29))
    page = _page([_char(ch, 486.2 + i * 5, 297.7, 491.0 + i * 5, 308.7) for i, ch in enumerate("4.36")])
    PDFParser._certify_native_blank_cells(page, [blank], "lines")
    assert blank.native_blank_verified is True


def test_glyph_centred_inside_cell_blocks_certification():
    blank = _cell((466.3, 307.54, 515.05, 323.29))
    page = _page([_char("8", 486.0, 309.0, 491.0, 320.0)])
    PDFParser._certify_native_blank_cells(page, [blank], "lines")
    assert blank.native_blank_verified is False


def test_deep_glyph_overlap_still_blocks_certification():
    # 字高 11pt，压进本格 5pt（>四成）——中心虽在格外，仍保守算本格墨迹。
    blank = _cell((466.3, 307.54, 515.05, 323.29))
    page = _page([_char("8", 486.0, 301.5, 491.0, 312.5)])
    PDFParser._certify_native_blank_cells(page, [blank], "lines")
    assert blank.native_blank_verified is False


# ---------------------------------------------------------------------------
# V33-112：连续 X 算一处，全文汇总成一条
# ---------------------------------------------------------------------------


def test_placeholder_runs_are_one_occurrence_and_aggregated():
    page = ("二、事业收入：指事业单位开展专业业务活动及其辅助活动\n取得的收入。主要是：XXXXXXXX 收入等。\n"
            "三、经营收入：指事业单位在专业业务活动及其辅助活动之外开展非独立核算经营活动取得的收入。"
            "主要是：XXXXXXXX 收入\n等。\n")
    doc = build_document("/tmp/sample.pdf", ["封面", page], [[], []], 1000)
    issues = R33112_PlaceholderCheck().apply(doc)
    assert len(issues) == 1
    issue = issues[0]
    assert "XXXXXXXX" in issue.message and "共 2 处" in issue.message
    assert issue.location["occurrences"] == 2
    assert [ref["page"] for ref in issue.location["table_refs"]] == [2, 2]


def test_placeholder_ignores_latin_words_and_reports_template_prompts():
    doc = build_document("/tmp/sample.pdf", ["Box XXL size 无占位", "单位名称：待填写"], [[], []], 1000)
    issues = R33112_PlaceholderCheck().apply(doc)
    assert len(issues) == 1 and "待填写" in issues[0].message


# ---------------------------------------------------------------------------
# 缺表/空表连带项
# ---------------------------------------------------------------------------


def _coverage_item(obligation_id, detail, depends_on, blocked_by=()):
    coverage = build_coverage({
        "catalog_version": "t", "applicable_total": 1, "completed_total": 0, "unresolved_total": 1,
        "not_applicable_total": 0, "blocking_total": 1,
        "instances": [{
            "obligation_id": obligation_id, "status": "insufficient_data", "reason": "insufficient_data",
            "detail": detail, "checkers": ["V33-241"], "blocks_gate": True,
            "depends_on": list(depends_on), "blocked_by": list(blocked_by),
        }],
    })
    assert coverage.available
    return coverage.items[0]


def _row(*findings):
    return {"rule_findings": list(findings)}


MISSING_T3 = {"id": "f-missing", "rule_id": "V33-002", "severity": "high", "message": "缺失表：支出决算表",
              "location": {"table": "支出决算表"}}
EMPTY_T7 = {"id": "f-empty", "rule_id": "V33-109", "severity": "high",
            "message": "【一般公共预算财政拨款“三公”经费支出决算表】为空表，但该页未见针对该表的空表说明。",
            "location": {"page": 16}}


def test_table_names_resolve_longest_first():
    assert _table_codes_in("一般公共预算财政拨款“三公”经费支出决算表") == {"FIN_07"}
    assert _table_codes_in("一般公共预算财政拨款基本支出决算表") == {"FIN_06"}
    assert _table_codes_in("财政拨款收入支出决算总表") == {"FIN_04"}
    assert _table_codes_in("收入支出决算总表 与 支出决算表") == {"FIN_01", "FIN_03"}
    assert _table_codes_in("FIN_03_expenditure没有可用金额事实") == {"FIN_03"}
    assert _table_codes_in("说明中的类“社会保障和就业支出”在表内找不到同名科目") == set()


def test_obligation_blocked_only_by_missing_table_hangs_under_root_issue():
    item = _coverage_item(
        "OBL-INTERNAL-T3-TOTAL",
        "FIN_03_expenditure没有可用金额事实；结构：阻断 FIN_03_expenditure（未识别到目标表）；阻断 规则级:V33-241（未识别到目标表）",
        ["table:FIN_03"],
        [{"dimension": "instance_uid", "ref": "FIN_03_expenditure", "reason_code": "table_missing"}],
    )
    assert cascade_root_issue_id(item, _row(MISSING_T3), visible_issue_ids={"f-missing"}) == "f-missing"


def test_obligation_blocked_by_empty_table_hangs_under_empty_table_issue():
    item = _coverage_item(
        "OBL-SG-TOTAL",
        "一般公共预算财政拨款“三公”经费支出决算表 没有可用金额事实；FIN_07_three_public没有可用金额事实",
        ["table:FIN_07"],
    )
    assert cascade_root_issue_id(item, _row(EMPTY_T7), visible_issue_ids={"f-empty"}) == "f-empty"


def test_mixed_causes_stay_in_required_queue():
    item = _coverage_item(
        "OBL-NARRATIVE-AMOUNT",
        "第19页原文关系未完成：原文包含其他或未确认主体，不能跨主体拼接金额；FIN_03_expenditure没有可用金额事实",
        ["table:FIN_03"],
    )
    assert cascade_root_issue_id(item, _row(MISSING_T3), visible_issue_ids={"f-missing"}) is None


def test_cascade_requires_visible_formal_root_and_declared_dependency():
    item = _coverage_item("OBL-INTERNAL-T3-TOTAL", "FIN_03_expenditure没有可用金额事实", ["table:FIN_03"])
    # 根问题被忽略（不在可见集合）或被降级为参考时，不能用它挂连带项。
    assert cascade_root_issue_id(item, _row(MISSING_T3), visible_issue_ids=set()) is None
    info_root = {**MISSING_T3, "severity": "info"}
    assert cascade_root_issue_id(item, _row(info_root), visible_issue_ids={"f-missing"}) is None
    # 义务声明依赖的是另一张表时不挂。
    other = _coverage_item("OBL-X", "FIN_03_expenditure没有可用金额事实", ["table:FIN_05"])
    assert cascade_root_issue_id(other, _row(MISSING_T3), visible_issue_ids={"f-missing"}) is None
