"""清单 E-02：空白页必须作为问题报出，不能静默通过。

背景（2026-10-25）：CMM-015 原先无条件跳过第 1 页、版面层读不到时静默跳过
整段检查，且“无文字但有矢量图形”的页既不算空白也不报。三处都会让空白页
从问题清单里消失。
"""
from pathlib import Path

import pymupdf
import pytest

from src.engine.checklist_structure_rules import CMM015_PageIntegrity
from src.engine.rule_outcome import RuleNotApplicable
from src.engine.rules_v33 import build_document
from src.services.ocr_pipeline import image_page_gate

BODY = ("一、收入决算总体情况 本年收入合计 12,345.67 万元 其中财政拨款收入 "
        "10,000.00 万元 事业收入 2,345.67 万元 支出合计 11,000.00 万元")


def _write_text_page(document, text: str = BODY) -> None:
    page = document.new_page(width=595, height=842)
    for offset in range(0, len(text), 25):
        page.insert_text((50, 80 + offset * 2), text[offset:offset + 25])


def _document(path: Path, *, blank_first: bool = False, blank_middle: bool = False) -> Path:
    document = pymupdf.open()
    if blank_first:
        document.new_page(width=595, height=842)
    _write_text_page(document)
    if blank_middle:
        document.new_page(width=595, height=842)
    _write_text_page(document)
    document.save(path)
    document.close()
    return path


def _rule_issues(path: Path):
    texts = [page.get_text() for page in pymupdf.open(path)]
    doc = build_document(str(path), texts, [[] for _ in texts], 0)
    return CMM015_PageIntegrity().apply(doc)


def test_blank_cover_page_is_reported(tmp_path):
    issues = _rule_issues(_document(tmp_path / "cover.pdf", blank_first=True))
    assert len(issues) == 1
    assert "封面" in issues[0].message and "第1页" in issues[0].message
    assert issues[0].severity == "info"


def test_blank_body_page_is_reported(tmp_path):
    issues = _rule_issues(_document(tmp_path / "body.pdf", blank_middle=True))
    assert len(issues) == 1
    assert "第2页为空白页" in issues[0].message
    assert issues[0].severity == "warn"


def test_page_with_only_vector_graphics_is_flagged_for_review(tmp_path):
    path = tmp_path / "vector.pdf"
    document = pymupdf.open()
    _write_text_page(document)
    page = document.new_page(width=595, height=842)
    page.draw_line((70, 80), (70, 110), color=(0, 0, 0), width=3)
    for x in (90, 115):
        page.draw_rect(pymupdf.Rect(x, 80, x + 15, 110), color=(0, 0, 0), width=3)
    document.save(path)
    document.close()

    issues = _rule_issues(path)
    assert len(issues) == 1
    assert "疑似内容未被识别" in issues[0].message
    assert issues[0].severity == "info"


def test_unreadable_layout_stops_the_rule_instead_of_passing():
    doc = build_document(str(Path("/tmp/does-not-exist-anywhere.pdf")), ["", BODY], [[], []], 0)
    with pytest.raises(RuleNotApplicable, match="空白页检查无法完成"):
        CMM015_PageIntegrity().apply(doc)


def test_no_textless_page_keeps_page_numbering_available():
    doc = build_document(str(Path("/tmp/does-not-exist-anywhere.pdf")), [BODY, BODY], [[], []], 0)
    assert CMM015_PageIntegrity().apply(doc) == []


def test_ocr_gate_keeps_sparse_vector_content_instead_of_calling_it_blank(tmp_path):
    path = tmp_path / "sparse.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.draw_line((70, 80), (70, 110), color=(0, 0, 0), width=3)
    for x in (90, 115):
        page.draw_rect(pymupdf.Rect(x, 80, x + 15, 110), color=(0, 0, 0), width=3)
    document.save(path)
    document.close()

    pages, blanks, error = image_page_gate(str(path), [1], no_text_pages={1})
    assert pages == [1] and blanks == [] and error is None


def test_ocr_gate_still_skips_a_truly_blank_page(tmp_path):
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    document.new_page(width=595, height=842)
    document.save(path)
    document.close()

    pages, blanks, error = image_page_gate(str(path), [1], no_text_pages={1})
    assert pages == [] and blanks == [1] and error is None
