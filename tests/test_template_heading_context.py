"""F2：独占编号属于标题块，不能隔断前置引用或否定语境。"""
from copy import deepcopy

import pytest

from src.engine.checklist_text_rules import CMM008_TemplateResidue
from src.services.template_anchor_checks import _headings, additional_anchor_issues
from tests.test_material_writing_rules import document
from tests.test_template_anchor_checks import fixture_resolution
from tests.test_template_wrapped_titles import titled_document, FOUR_LINES, TITLE, wrapped


@pytest.mark.parametrize("prefix", ["引用：", "本报告尚未提供", "例如："])
@pytest.mark.parametrize("text", ["新增政策\n执行情况\n说明", "新增\n政策执行\n情况\n说明",
                                  "\n".join("新增政策执行情况说明")], ids=["three", "four", "many"])
def test_quoted_numbered_section_is_not_a_real_heading(prefix, text):
    title = "新增政策执行情况说明"
    anchor = {"kind": "normative_section", "scope": "all", "text": title, "basis": "independent F2 counterexample"}
    doc = document(["2025年度决算", prefix + "\n一、\n" + text], fixture_resolution([anchor]))
    assert not _headings(doc, title)
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_section")
    assert issues or pending


@pytest.mark.parametrize("kind", ["normative_section", "normative_table"])
@pytest.mark.parametrize("prefix", ["正文详见", "本报告未编制", "引用："])
@pytest.mark.parametrize("number", ["一、", "(一)", "1.", "第十一部分", "一、\n（一）"])
def test_reference_context_propagates_through_standalone_numbers(kind, prefix, number):
    doc = titled_document(prefix + "\n" + number + "\n" + FOUR_LINES, kind)
    before = deepcopy(doc.parse_artifact.to_dict())
    assert not _headings(doc, TITLE)
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, kind)
    assert issues or pending
    assert doc.parse_artifact.to_dict() == before


@pytest.mark.parametrize("kind", ["normative_section", "normative_table"])
@pytest.mark.parametrize("text", [FOUR_LINES, wrapped(len(TITLE))], ids=["four", "many"])
@pytest.mark.parametrize("preceding", ["", "前置说明。\n", "引用：\n以上示例已经结束。\n"])
def test_real_numbered_heading_still_passes_after_body_text(kind, text, preceding):
    doc = titled_document(preceding + "一、\n" + text, kind)
    assert _headings(doc, TITLE)
    assert additional_anchor_issues(CMM008_TemplateResidue(), doc, kind) == ([], [])


def test_crlf_indent_and_number_lines_cannot_hide_quoted_context():
    text = "引用：\r\n  第三部分  \r\n  （一）  \r\n" + "\r\n".join("  " + part for part in FOUR_LINES.splitlines())
    doc = titled_document(text)
    assert not _headings(doc, TITLE)
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table")
    assert issues or pending


def test_context_does_not_propagate_from_previous_page():
    doc = titled_document("一、\n" + FOUR_LINES)
    doc.page_texts.insert(1, "引用：")
    # 对应表实例也必须随物理页移动；否则只能得到待核，不能证明正常标题通过。
    doc.parse_artifact.table_fragments[0].page_number = 3
    doc.parse_artifact.logical_tables[0].page_start = 3
    doc.parse_artifact.logical_tables[0].page_end = 3
    doc.parse_artifact.page_count = 3
    assert _headings(doc, TITLE)
    assert additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table") == ([], [])
