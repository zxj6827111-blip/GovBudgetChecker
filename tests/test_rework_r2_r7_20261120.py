"""R2–R7 返工回归（2026-11-20 第二轮，Codex 独立复审六项）。

覆盖：
- R3：校正目标保留完整异常字串；CMM-011 引文不截断；非法原读数不得只确认单位。
- R4：多次改数按台账顺序生效（单项与加总同值）；科目指定后单项与加总同依据。
- R5：同页两笔（含同额）各改各的；预算行 raw="0" 不再误配；无法区分位置保持待核；
      队列口径按已表态范围扣减。
- R7：合计收口不得并入后续片段；数量列不冒充科目编码；真续表继续归表。
- R2：原文单位与核算单位分别留痕（原文未写单位不得写成“原文披露…万元”）。

真实上传/发布/浏览器链路在隔离环境验收（outputs/zcode_rework2_20261120/）。
"""

from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace

from src.engine.checklist_final_rules import (
    R33251_FunctionalDetailCompleteness,
    _apply_text_corrections,
    _compact_raw_token_in,
)
from src.engine.checklist_text_rules import CMM011_AmountUnitWan
from src.engine.native_final_disclosure import (
    _BareClaimWithUnit,
    _manual_correction_for_item,
)
from src.engine.native_narrative_checks import _Text
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser
from src.services.text_correction_targets import build_text_correction_targets


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def _correction(**overrides):
    base = {
        "correction_id": "cor-test",
        "field": "raw_value",
        "value": "5455.41",
        "page": 1,
        "span": "支出决算为5,465.41",
        "actor": "tester",
        "analysis_basis_token": "job:b1",
        "reason": "test",
    }
    base.update(overrides)
    return base


def _text_document(page_text: str) -> _Text:
    return _Text.from_pages([page_text])


class _Claim:
    """与正则 match 同接口的声明视图；unit 默认按原文显式单位处理。"""

    def __init__(self, amount, unit, start, end, inherited=False):
        self._data = {"amount": amount, "unit": unit}
        self._start, self._end = start, end
        self.inherited_unit = inherited

    def __getitem__(self, key):
        return self._data[key]

    def start(self):
        return self._start

    def end(self):
        return self._end


# ---------------------------------------------------------------------------
# R3：校正目标与 CMM-011 引文保留完整异常字串
# ---------------------------------------------------------------------------

def _payload_with_cmm011(message: str):
    return {
        "analysis_revision": "rev-test",
        "result": {
            "issues": {
                "warn": [{
                    "id": "CMM-011-1",
                    "rule_id": "CMM-011",
                    "title": "金额缺少单位“万元”",
                    "message": message,
                    "location": {"page": 26, "pos": 120},
                    "evidence": [{
                        "physical_page": 26,
                        "quote": "支出决算为5,465.41.00",
                        "bbox": [10, 20, 30, 40],
                        "verification_status": "verified",
                    }],
                }],
                "error": [], "info": [], "all": [],
            },
        },
        "review_report": {"check_receipts": [{"rule_id": "CMM-011"}, {"rule_id": "V33-222"}, {"rule_id": "V33-251"}]},
    }


def test_r3_target_keeps_full_double_dot_literal():
    payload = _payload_with_cmm011(
        "金额缺少单位“万元”：“支出决算为5,465.41.00”，同段其余金额均以万元列示。")
    targets = build_text_correction_targets(payload)
    assert len(targets) == 1
    target = targets[0]
    assert target["amount_raw"] == "5,465.41.00"
    assert target["raw_is_valid"] is False
    raw_action = next(action for action in target["actions"] if action["field"] == "raw_value")
    assert raw_action["value"] == "5,465.41.00"


def test_r3_target_keeps_full_malformed_grouping_literal():
    payload = _payload_with_cmm011(
        "金额缺少单位“万元”：“支出决算为54,65.41”，同段其余金额均以万元列示。")
    targets = build_text_correction_targets(payload)
    assert targets[0]["amount_raw"] == "54,65.41"
    assert targets[0]["raw_is_valid"] is False


def test_r3_valid_control_is_marked_valid():
    payload = _payload_with_cmm011(
        "金额缺少单位“万元”：“支出决算为5,465.41”，同段其余金额均以万元列示。")
    targets = build_text_correction_targets(payload)
    assert targets[0]["amount_raw"] == "5,465.41"
    assert targets[0]["raw_is_valid"] is True


def test_r3_cmm011_issue_quote_covers_double_dot():
    page = (
        "十九、节能环保支出（类）污染减排（款）减排专项支出（项）。"
        "年初预算为0 万元，支出决算为5,465.41.00，决算数小于预算数的主要原因。"
        "其他金额如 100.00 万元以万元列示。"
    )
    issues = CMM011_AmountUnitWan().apply(
        SimpleNamespace(page_texts=[page], page_bboxes=[None], document_profile=None))
    messages = [issue.message for issue in issues]
    assert any("5,465.41.00" in message for message in messages), messages


# ---------------------------------------------------------------------------
# R4：多次改数按台账顺序生效；科目指定同依据
# ---------------------------------------------------------------------------

PAGE_26 = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "甲单位2023年度一般公共预算财政拨款支出决算。其中：\n"
    "1、商业服务业等支出（类）商业流通事务（款）事业运行（项）。"
    "主要用于：事业单位运行经费。年初预算为5,764.81 万元，支出决算为5,465.41，"
    "决算数小于预算数的主要原因：部分支出未执行。\n"
)


def _item_for(text: _Text, needle: str):
    start = text.compact.find(needle)
    assert start >= 0
    return SimpleNamespace(start=lambda: start, end=lambda: start + len(needle))


def _manual(doc_corrections, claim=None, text=None):
    text = text or _text_document(PAGE_26)
    item = _item_for(text, "商业服务业等支出")
    claim = claim or _Claim("5,465.41", "万元", text.compact.find("5,465.41"),
                            text.compact.find("5,465.41") + 8, inherited=True)
    doc = SimpleNamespace(text_corrections=doc_corrections)
    return _manual_correction_for_item(doc, text, item, len(text.compact), claim)


def test_r4_second_correction_wins_in_ledger_order():
    first = _correction(correction_id="cor-1", value="5455.41", analysis_basis_token="job:b1")
    second = _correction(correction_id="cor-2", value="5445.41", analysis_basis_token="job:b2")
    manual = _manual([first, second])
    assert manual is not None
    assert manual["amount"] == "5445.41"
    assert manual["correction_ids"] == ["cor-1", "cor-2"]
    assert manual["actor"] == "tester"


def test_r4_same_version_conflicting_values_stay_unresolved():
    first = _correction(correction_id="cor-1", value="5455.41", analysis_basis_token="job:b1")
    second = _correction(correction_id="cor-2", value="5445.41", analysis_basis_token="job:b1")
    assert _manual([first, second]) is None


def test_r4_subject_designation_passes_through():
    manual = _manual([_correction(field="subject_code", value="2011350")])
    assert manual is not None
    assert manual["subject_code"] == "2011350"
    assert "subject_name" not in manual


def test_r4_subject_designation_with_conflicting_name_stays_unresolved_downstream():
    """指定编码+矛盾名称：单项不能拿原行判通过（由调用方落到待核，见 V33-222 流程）。"""
    manual = _manual([
        _correction(field="subject_code", value="2011350"),
        _correction(field="subject_name", value="其他名字"),
    ])
    assert manual is not None
    assert manual["subject_code"] == "2011350"
    assert manual["subject_name"] == "其他名字"


# ---------------------------------------------------------------------------
# R5：同页逐笔独立生效；无法区分位置保持待核；队列口径扣减
# ---------------------------------------------------------------------------

def _located_records(*specs):
    """specs: (raw, span_compact, span_raw, page_start) → located 条目。

    locate 返回的 ``text`` 是页文本（页首填充空格到 page_start，使
    text[start:end] 恰为 span_raw），与解析器的页内偏移语义一致。
    """
    records = []
    locate_spans = []
    for raw, span_compact, span_raw, page_start in specs:
        record = {"pos": len(locate_spans), "raw": raw, "kind": "final", "unit": None}
        records.append(record)
        locate_spans.append((span_compact, span_raw, page_start))

    def locate(pos):
        span_compact, span_raw, page_start = locate_spans[pos]
        page_text = " " * page_start + span_raw
        return 1, page_text, page_start, page_start + len(span_raw)

    return records, locate


def _apply(records, locate, corrections):
    doc = SimpleNamespace(text_corrections=corrections)
    count = _apply_text_corrections(records, locate, doc)
    return doc, count


def test_r5_zero_budget_row_is_not_matched_by_amount_token():
    records, locate = _located_records(
        ("0", "年初预算为0万元，支出决算为12", "年初预算为0万元，支出决算为12", 100),
        ("12,906.51", "支出决算为12,906.51。决", "支出决算为12,906.51。决", 140),
    )
    doc, count = _apply(records, locate, [
        _correction(value="5300.01", span="支出\n决算为12,906.51"),
        _correction(field="unit", value="万元", span="支出\n决算为12,906.51"),
    ])
    assert count == 1
    assert records[1]["raw"] == "5300.01"
    assert records[0]["raw"] == "0"
    assert not getattr(doc, "unapplied_text_corrections", None)


def test_r5_same_amount_two_sentences_each_take_own_correction():
    records, locate = _located_records(
        ("5,465.41", "支出决算为5,465.41，决算", "支出决算为5,465.41，决算", 100),
        ("5,465.41", "支出决算为5,465.41。决", "支出 决算为5,465.41。决", 140),
    )
    doc, count = _apply(records, locate, [
        _correction(correction_id="c1", value="5444.41", span="元，支出决算为5,465.41，决算数"),
        _correction(correction_id="c1", field="unit", value="万元", span="元，支出决算为5,465.41，决算数"),
        _correction(correction_id="c2", value="5300.01", span="万元，支出\n决算为5,465.41。决算数"),
        _correction(correction_id="c2", field="unit", value="万元", span="万元，支出\n决算为5,465.41。决算数"),
    ])
    assert count == 2
    assert records[0]["raw"] == "5444.41"
    assert records[1]["raw"] == "5300.01"
    assert not getattr(doc, "unapplied_text_corrections", None)


def test_r5_identical_sentences_without_pos_stay_pending():
    records, locate = _located_records(
        ("5,465.41", "支出决算为5,465.41。决", "支出决算为5,465.41。决", 100),
        ("5,465.41", "支出决算为5,465.41。决", "支出决算为5,465.41。决", 200),
    )
    doc, count = _apply(records, locate, [
        _correction(correction_id="c1", value="5444.41", span="支出决算为5,465.41"),
        _correction(correction_id="c1", field="unit", value="万元", span="支出决算为5,465.41"),
    ])
    assert count == 0
    reasons = {item["reason"] for item in doc.unapplied_text_corrections}
    assert reasons == {"ambiguous_match"}
    assert records[0]["raw"] == "5,465.41"
    assert records[1]["raw"] == "5,465.41"


def test_r5_correction_pos_picks_exact_row():
    """真正同形原句：带 pos 的校正各落各的，不再一次改动两笔。"""
    records, locate = _located_records(
        ("5,465.41", "支出决算为5,465.41。决", "支出决算为5,465.41。决", 100),
        ("5,465.41", "支出决算为5,465.41。决", "支出决算为5,465.41。决", 200),
    )
    doc, count = _apply(records, locate, [
        _correction(correction_id="c1", value="5444.41", span="支出决算为5,465.41", pos=100),
        _correction(correction_id="c1", field="unit", value="万元", span="支出决算为5,465.41", pos=100),
        _correction(correction_id="c2", value="5300.01", span="支出决算为5,465.41", pos=200),
        _correction(correction_id="c2", field="unit", value="万元", span="支出决算为5,465.41", pos=200),
    ])
    assert count == 2
    assert records[0]["raw"] == "5444.41"
    assert records[1]["raw"] == "5300.01"


def test_r5_compact_raw_token_requires_boundaries():
    assert _compact_raw_token_in("支出决算为12,906.51", "0") is False
    assert _compact_raw_token_in("支出决算为12,906.51", "12,906.51") is True
    assert _compact_raw_token_in("支出决算为12,906.51", "906") is False


def test_r5_queue_counts_deduct_processed_scopes():
    from api.runtime import apply_scope_decisions_to_summary

    scopes = [
        {"scope": f"grp:{index}", "obligations": ["OBL-TXT-T5"], "affected": 1}
        for index in range(4)
    ] + [{"scope": "grp:final", "obligations": ["OBL-TXT-FINAL-DETAIL"], "affected": 1}]
    summary = {
        "queue_unfinished_blocking_scopes": scopes,
        "queue_unfinished_blocking_location_total": 5,
        "queue_unfinished_blocking_check_total": 5,
        "queue_unfinished_blocking_affected_total": 5,
    }
    # 第一批表态（3 处：第 24 页两处 + 分项加总范围）：5 → 2。
    apply_scope_decisions_to_summary(summary, {
        "OBL-TXT-T5": {f"grp:{index}": "verified_issue" for index in range(2)},
        "OBL-TXT-FINAL-DETAIL": {"grp:final": "verified_ok"},
    })
    assert summary["queue_unfinished_blocking_location_total"] == 2
    assert summary["queue_unfinished_blocking_check_total"] == 2
    assert summary["queue_unfinished_blocking_affected_total"] == 2
    # 再处理“减排专项支出”：2 → 1，剩余位置精确指向未表态那处（事业运行）。
    apply_scope_decisions_to_summary(summary, {
        "OBL-TXT-T5": {f"grp:{index}": "verified_issue" for index in range(3)},
        "OBL-TXT-FINAL-DETAIL": {"grp:final": "verified_ok"},
    })
    assert summary["queue_unfinished_blocking_location_total"] == 1
    assert summary["queue_unfinished_blocking_affected_total"] == 1


def test_r5_queue_counts_multi_scope_requires_every_scope():
    from api.runtime import apply_scope_decisions_to_summary

    scopes = [
        {"scope": "grp:a", "obligations": ["OBL-X"], "affected": 1},
        {"scope": "grp:b", "obligations": ["OBL-X"], "affected": 1},
    ]
    summary = {"queue_unfinished_blocking_scopes": scopes}
    # 只处理一处：已处理位置从待办中扣除（2 → 1），未处理的那处继续阻塞。
    apply_scope_decisions_to_summary(summary, {"OBL-X": {"grp:a": "verified_ok"}})
    assert summary["queue_unfinished_blocking_location_total"] == 1
    assert summary["queue_unfinished_blocking_affected_total"] == 1
    apply_scope_decisions_to_summary(summary, {"OBL-X": {"grp:a": "verified_ok", "grp:b": "verified_issue"}})
    assert summary["queue_unfinished_blocking_location_total"] == 0


def test_r5_queue_counts_single_scope_honours_obligation_level_decision():
    from api.runtime import apply_scope_decisions_to_summary

    summary = {"queue_unfinished_blocking_scopes": [
        {"scope": "grp:a", "obligations": ["OBL-Y"], "affected": 2},
    ]}
    apply_scope_decisions_to_summary(summary, {"OBL-Y": {"": "not_applicable"}})
    assert summary["queue_unfinished_blocking_location_total"] == 0


# ---------------------------------------------------------------------------
# R7：续表身份与金额列
# ---------------------------------------------------------------------------

def _table(code, page, rows, header, widths, title=""):
    cells = []
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            x0 = sum(widths[:col_index])
            x1 = x0 + widths[col_index]
            cells.append(ParsedCell(
                row_index, col_index, str(value), str(value), None, page,
                (x0, 100 + row_index * 20, x1, 120 + row_index * 20),
                row_index < header, 0.95, "万元", "lines",
            ))
    return ParsedTable(
        table_code=code,
        title=title,
        page_number=page,
        table_index=0,
        bbox=(0, 100, sum(widths), 100 + 20 * len(rows)),
        header_row_count=header,
        confidence=0.9,
        extraction_method="lines",
        unit_hint="万元",
        rows=rows,
        cells=cells,
        fragment_uid=f"p{page}-{code}",
    )


FIN_05_CLOSED = (
    ["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
    ["2160250", "事业运行", "100", "80", "20"],
    ["合计", "", "100", "80", "20"],
)
DEVICES = [["办公桌", "台", "3", "1", "2"], ["电脑", "台", "4", "2", "2"]]


def test_r7_total_closed_table_never_absorbs_same_geometry_fragment():
    parser = PDFParser(None)
    previous = _table("FIN_05_general_public_expenditure", 1, FIN_05_CLOSED, 1,
                      [50, 140, 60, 60, 60], "一般公共预算财政拨款支出决算表")
    fragment = _table("UNKNOWN_P2_T0", 2, DEVICES, 0, [50, 140, 60, 60, 60])
    assert parser._native_continuation_columns_match(previous, fragment) is True
    assert parser._is_continuation_table(previous, fragment) is False
    merged = parser._coalesce_tables([previous, fragment])
    assert [table.table_code for table in merged] == [
        "FIN_05_general_public_expenditure", "UNKNOWN_P2_T0",
    ]
    assert ["办公桌", "台", "3", "1", "2"] not in merged[0].rows


def test_r7_count_column_is_not_a_subject_code():
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["205", "教育支出", "100", "80", "20"]],
        1, [50, 140, 60, 60, 60], "一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0", 2,
        [["办公桌", "300", "180", "80", "100"], ["电脑", "400", "220", "100", "120"]],
        0, [140, 40, 40, 30, 120],
    )
    assert parser._row_subject_code_column(["办公桌", "300", "180"])[0] is None or \
        parser._row_subject_code_column(["办公桌", "300", "180"])[1] == 1
    assert parser._continuation_row_sequence_evidence(previous, fragment) is False
    assert parser._is_continuation_table(previous, fragment) is False
    merged = parser._coalesce_tables([previous, fragment])
    assert [table.table_code for table in merged] == [
        "FIN_05_general_public_expenditure", "UNKNOWN_P2_T0",
    ]


def test_r7_true_continuation_with_different_widths_still_merges():
    parser = PDFParser(None)
    previous = _table(
        "FIN_05_general_public_expenditure", 1,
        [["科目编码", "科目名称", "合计", "基本支出", "项目支出"],
         ["20502", "普通教育", "100", "80", "20"]],
        1, [50, 140, 60, 60, 60], "一般公共预算财政拨款支出决算表",
    )
    fragment = _table(
        "UNKNOWN_P2_T0", 2,
        [["2050201", "学前教育", "60", "50", "10"], ["2050202", "小学教育", "40", "30", "10"]],
        0, [140, 40, 40, 30, 120],
    )
    assert parser._continuation_row_sequence_evidence(previous, fragment) is True
    assert parser._is_continuation_table(previous, fragment) is True
    merged = parser._coalesce_tables([previous, fragment])
    assert len(merged) == 1
    assert ["2050201", "学前教育", "60", "50", "10"] in merged[0].rows
    assert ["2050202", "小学教育", "40", "30", "10"] in merged[0].rows


# ---------------------------------------------------------------------------
# R2：原文单位与核算单位分别留痕
# ---------------------------------------------------------------------------

def test_r2_bare_claim_correction_reports_missing_unit_not_table_unit():
    text = _text_document(PAGE_26)
    manual = _manual(
        [_correction(value="5455.41"), _correction(field="unit", value="万元")],
        claim=_Claim("5,465.41", "万元", text.compact.find("5,465.41"),
                     text.compact.find("5,465.41") + 8, inherited=True),
        text=text,
    )
    assert manual is not None
    assert manual["original_unit"] is None
    assert manual["original_unit_written"] is False


def test_r2_explicit_unit_correction_keeps_written_unit():
    text = _text_document(PAGE_26)
    manual = _manual(
        [_correction(value="5455.41"), _correction(field="unit", value="万元")],
        claim=_Claim("5,465.41", "万元", text.compact.find("5,465.41"),
                     text.compact.find("5,465.41") + 8, inherited=False),
        text=text,
    )
    assert manual is not None
    assert manual["original_unit"] == "万元"
    assert manual["original_unit_written"] is True
    assert manual["unit_field_applied"] is True


def test_r2_manual_unit_confirmed_as_yuan_is_marked_reviewer_confirmed():
    text = _text_document(PAGE_26)
    manual = _manual(
        [_correction(field="unit", value="元")],
        claim=_Claim("5,465.41", "万元", text.compact.find("5,465.41"),
                     text.compact.find("5,465.41") + 8, inherited=True),
        text=text,
    )
    assert manual is not None
    assert manual["unit"] == "元"
    assert manual["unit_field_applied"] is True
    assert manual["original_unit"] is None


# ---------------------------------------------------------------------------
# R4/R5 规则层联动：单项与加总消费同一有效读数（合成页面）
# ---------------------------------------------------------------------------

V251_PAGE = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n"
    "1、商业服务业等支出（类）商业流通事务（款）事业运行（项）。"
    "主要用于：事业单位运行经费。年初预算为5,764.81 万元，支出决算为5,465.41，决算数小于预算数。\n"
    "六、一般公共预算财政拨款基本支出决算情况说明"
)


def test_r4_two_sequential_edits_keep_single_item_and_sum_consistent():
    """加总侧与单项同序消费：跨版本两次改数，后值生效、两次记录都留痕。"""
    records, locate = _located_records(
        ("5,465.41", "支出决算为5,465.41，决算", "支出决算为5,465.41，决算", 100),
    )
    doc, count = _apply(records, locate, [
        _correction(correction_id="cor-1", value="5455.41", analysis_basis_token="job:b1"),
        _correction(correction_id="cor-1", field="unit", value="万元", analysis_basis_token="job:b1"),
        _correction(correction_id="cor-2", value="5445.41", analysis_basis_token="job:b2"),
        _correction(correction_id="cor-2", field="unit", value="万元", analysis_basis_token="job:b2"),
    ])
    assert count == 1
    assert records[0]["raw"] == "5445.41"
    manual_corrections = records[0].get("manual_corrections") or []
    raw_values = [item.get("value") for item in manual_corrections if item.get("field") == "raw_value"]
    assert raw_values == ["5455.41", "5445.41"]
    assert [item.get("actor") for item in manual_corrections if item.get("field") == "raw_value"] == ["tester", "tester"]
    assert [item.get("analysis_basis_token") for item in manual_corrections if item.get("field") == "raw_value"] == ["job:b1", "job:b2"]
    assert records[0]["value_wan"] == Decimal("5445.41")
    assert not getattr(doc, "unapplied_text_corrections", None)


def test_r3_malformed_reading_blocks_sum_and_keeps_literal():
    doc = build_document("/tmp/rework-r3.pdf", [V251_PAGE], [[]], 1000)
    doc.report_kind = "final"
    doc.fact_context = SimpleNamespace(
        version_token="v", file_hash="a" * 64, parse_run_uid="r",
        org_name="甲单位", fiscal_year=2023,
    )
    # 人工也不得把截断前缀当读数：非法值必须在入口被拒（服务端 _AMOUNT_TEXT），
    # 这里验证规则层对未确认读数保持 insufficient_data。
    rule = R33251_FunctionalDetailCompleteness()
    try:
        rule.apply(doc)
        signal = None
    except RuleOutcomeSignal as exc:
        signal = exc
    assert signal is not None and signal.status == "insufficient_data"
