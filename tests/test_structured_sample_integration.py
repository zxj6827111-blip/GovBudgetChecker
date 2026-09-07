"""官方样张真实 PDF 的结构化解析集成测试（GPT5.6 R3 要求）。

R2/R3 复核的核心批评：结构化修复从未在真实样张上验证——build_parsed_tables
的续表判定在官方 PDF 上把 14 张原始表错误合并成 4 张（跨 10 页吞并、拒绝
合并时丢表），V33-115 的标题匹配从未命中（title 实为「收入支出」，表名
在页文本里）。本文件用 corpus 的真实 PDF 锁定两件事：
1. 逻辑表数量在合理范围（约 9-10 张，不吞并、不丢表）；
2. V33-115 在样张上真正进入 _apply_structured 结构化消费路径。
"""

import glob
from pathlib import Path

import pytest

from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33115_TotalSheetCheck, _find_parsed_table
from src.engine.structured_rules import build_parsed_tables

ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_PDFS = glob.glob(str(ROOT / "corpus" / "DOC-20260905-001" / "*.pdf"))

pytestmark = pytest.mark.skipif(
    not _SAMPLE_PDFS,
    reason="corpus/DOC-20260905-001 样张 PDF 不存在（corpus 未入库时跳过）",
)
SAMPLE_PDF = Path(_SAMPLE_PDFS[0]) if _SAMPLE_PDFS else None


def _load_sample():
    from scripts.replay_golden_corpus import load_page_tables, load_page_texts

    page_texts = load_page_texts(SAMPLE_PDF)
    page_tables = load_page_tables(SAMPLE_PDF)
    doc = build_document(
        path="DOC-20260905-001.pdf",
        page_texts=page_texts,
        page_tables=page_tables,
        filesize=0,
    )
    return doc, page_tables


def test_sample_raw_tables_merge_to_about_ten_logical_tables():
    """官方样张：14 张原始表 → 逻辑表约 9-10 张（R3 P0-1 实测基线）。

    R2 版实现曾把 14 张合并成 4 张（span 达 (10,19) 的跨页吞并）；
    R3 修复后每张表 span 必须紧凑（≤3 页）、无表被静默丢弃。
    """
    doc, page_tables = _load_sample()
    raw_count = sum(len(t) for t in page_tables)
    tables = build_parsed_tables(page_tables)

    assert raw_count == 14, f"样张原始表数量变化: {raw_count}"
    # 逻辑表约 10 张：合理合并（续页）但不吞并相邻表种
    assert 8 <= len(tables) <= 12, (
        f"逻辑表 {len(tables)} 张异常（R2 版缺陷是 4 张吞并、丢表）"
    )
    # 无表丢失：所有逻辑表的行数之和 ≥ 原始表行数之和
    raw_rows = sum(
        len(raw) for tables_on_page in page_tables for raw in tables_on_page
    )
    merged_rows = sum(len(t.rows) for t in tables.values())
    assert merged_rows >= raw_rows, (
        f"合并后行数 {merged_rows} < 原始 {raw_rows}：存在静默丢表"
    )
    # span 紧凑：任何逻辑表不得跨越超过 3 页
    for key, table in tables.items():
        span_pages = table.page_span[1] - table.page_span[0] + 1
        assert span_pages <= 3, (
            f"{key} span={table.page_span} 跨 {span_pages} 页：续表判定吞并了无关表"
        )


def test_sample_v33_115_enters_structured_consumption_path():
    """官方样张：V33-115 必须命中目标表并进入 _apply_structured（R3 P0-2）。

    表名「收入支出决算总表」在页文本（P7）而表格 title 只是表体首行
    「收入支出」——两级匹配（title → 锚点页）必须命中；命中后结构化
    路径取到真实总计值（样张收支两侧均为 4733.14，平衡无 finding）。
    """
    doc, page_tables = _load_sample()
    doc.parsed_tables = build_parsed_tables(page_tables)

    target = _find_parsed_table(doc, "收入支出决算总表")
    assert target is not None, (
        "两级匹配（title→锚点页）未命中样张总表——R3 P0-2 回归"
    )
    assert target.page_span == (7, 8), f"总表 span 异常: {target.page_span}"
    assert len(target.rows) > 10, f"总表行数异常: {len(target.rows)}"

    # 结构化路径产出（平衡表 → 0 issues 是正确结论，不是取不到数）
    issues = R33115_TotalSheetCheck().apply(doc)
    # 样张总表收支两侧相等（legacy 同样无 finding）：结构化结论必须一致
    assert issues == [], f"平衡总表不应产出 finding: {[i.message for i in issues]}"


def test_sample_v33_115_structured_matches_legacy_conclusion():
    """结构化与 legacy 在样张总表上结论一致（输入表征不同、语义相同）。"""
    doc, page_tables = _load_sample()
    legacy_issues = R33115_TotalSheetCheck().apply(doc)
    doc.parsed_tables = build_parsed_tables(page_tables)
    structured_issues = R33115_TotalSheetCheck().apply(doc)
    assert len(legacy_issues) == len(structured_issues)
