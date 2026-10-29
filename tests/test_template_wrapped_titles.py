"""F1：折行只改变排版，不应改变附加标题与对应表实例的判定。"""
from copy import deepcopy

import pytest

from src.engine.checklist_text_rules import CMM008_TemplateResidue
from src.services.template_anchor_checks import _headings, additional_anchor_issues
from tests.test_annual_template_p1_regressions import table_document

TITLE = "上海市普陀区某某事业发展中心2024年度一般公共预算财政拨款基本支出决算表"
THREE_LINES = "上海市普陀区某某事业发展中心\n2024年度一般公共预算\n财政拨款基本支出决算表"
FOUR_LINES = "上海市普陀区某某事业发展中心\n2024年度一般公共预算\n财政拨款基本支出\n决算表"


def wrapped(line_count):
    return "\n".join(TITLE[i * len(TITLE) // line_count:(i + 1) * len(TITLE) // line_count]
                     for i in range(line_count))


def titled_document(text, kind="normative_table"):
    doc = table_document()
    doc.page_texts[1] = text
    anchor = doc.document_profile.writing_scope["snapshot"]["content"]["config"]["anchors"][0]
    anchor.update(text=TITLE, kind=kind)
    doc.parse_artifact.table_fragments[0].title = TITLE
    doc.parse_artifact.logical_tables[0].title = TITLE
    return doc


@pytest.mark.parametrize("kind", ["normative_table", "normative_section"])
@pytest.mark.parametrize("text", [THREE_LINES, FOUR_LINES, wrapped(5), wrapped(8), wrapped(len(TITLE))],
                         ids=["three", "four", "five", "eight", "one-character-per-line"])
def test_complete_wrapped_title_has_no_fixed_line_limit(kind, text):
    doc = titled_document(text, kind)
    before = deepcopy(doc.parse_artifact.to_dict())
    assert additional_anchor_issues(CMM008_TemplateResidue(), doc, kind) == ([], [])
    assert len(_headings(doc, TITLE)) == 1
    assert doc.parse_artifact.to_dict() == before


@pytest.mark.parametrize("line_count", range(1, len(TITLE) + 1))
def test_all_contiguous_wrappings_preserve_the_same_title(line_count):
    doc = titled_document(wrapped(line_count))
    assert additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table") == ([], [])


def test_numbered_indented_wrapped_title_preserves_raw_text_offsets():
    text = "前置说明。\r\n  三、\r\n" + "\r\n".join("  " + p + "  " for p in FOUR_LINES.splitlines()) + "\r\n后置说明。"
    doc = titled_document(text)
    # 编号独占一行时可能存在重叠候选；核对包含编号的原件范围。
    heading = next(h for h in _headings(doc, TITLE) if h["start"] == text.index("三、"))
    assert heading["page"] == 2
    assert heading["start"] == text.index("三、")
    assert heading["end"] == text.index("后置说明。") - len("  \r\n")
    assert "".join(text[heading["start"]:heading["end"]].split()) == "三、" + TITLE
    assert additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table") == ([], [])


@pytest.mark.parametrize("prefix", ["本报告尚未提供", "正文详见", "引用：", "本报告未编制", "例如："])
@pytest.mark.parametrize("text", [FOUR_LINES, wrapped(len(TITLE))], ids=["four", "many"])
def test_wrapped_reference_or_negative_context_never_completes_title(prefix, text):
    doc = titled_document(prefix + "\n" + text)
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table")
    assert not _headings(doc, TITLE)
    assert issues or pending


@pytest.mark.parametrize("separator", ["\n\n", "；\n", "\n其他正文\n"])
def test_wrapping_cannot_cross_blank_sentence_or_unrelated_text(separator):
    doc = titled_document(separator.join(FOUR_LINES.rsplit("\n", 1)))
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table")
    assert not _headings(doc, TITLE)
    assert issues or pending


def test_wrapping_cannot_cross_physical_pages():
    doc = titled_document(FOUR_LINES.rsplit("\n", 1)[0])
    doc.page_texts.append(FOUR_LINES.rsplit("\n", 1)[1])
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table")
    assert not _headings(doc, TITLE)
    assert issues or pending


@pytest.mark.parametrize("defect", ["unrelated-table", "wrong-page", "no-instance", "duplicate-instance", "unbound-ocr"])
def test_wrapped_table_title_still_requires_one_qualified_instance(defect):
    doc = titled_document(FOUR_LINES)
    table = doc.parse_artifact.table_fragments[0]
    if defect == "unrelated-table":
        table.title = "另一张无关表"
    elif defect == "wrong-page":
        table.page_number = 1
    elif defect == "no-instance":
        doc.parse_artifact.logical_tables = []
    elif defect == "duplicate-instance":
        other = deepcopy(doc.parse_artifact.logical_tables[0])
        other.instance_uid = "another-instance"
        doc.parse_artifact.logical_tables.append(other)
    elif defect == "unbound-ocr":
        table.extraction_method = "ocr"
    issues, pending = additional_anchor_issues(CMM008_TemplateResidue(), doc, "normative_table")
    assert not issues
    assert any("对应表实例" in p for p in pending)
