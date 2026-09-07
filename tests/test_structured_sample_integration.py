"""官方样张结构化解析集成测试（GPT5.6 R4 P1-5：fail-closed，CI 可复现）。

R3 版问题：corpus/ 被 .gitignore 排除，干净 checkout 无样张 PDF，
skipif 让三项关键测试在 CI 上静默跳过。R4 修复：解析产物
（page_texts/page_tables，公开决算材料）序列化为入库夹具
（tests/fixtures/sample_page_data.json，含源 PDF SHA-256 溯源），
测试基于夹具 fail-closed 运行；本地有真实 PDF 时做 SHA 交叉校验
（夹具与 PDF 不一致即失败——防止夹具过期或被篡改）。

锁定的业务基线（R4 表名锚语义，样张实测）：
- 12 张逻辑表 = 10 个页文本表名各归各位 + P9/P11 两个无表名续页的
  保守独立；真续表（P12→13、P15→16）正确合并；
- 跨表种合并（P7+8 / P15-17 / P18+19，R3 版缺陷）必须为零；
- V33-115 命中总表 (7,7)、取到真实总计 4733.14、进入结构化路径。
"""

import glob
import hashlib
import json
from pathlib import Path

import pytest

from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33115_TotalSheetCheck, _find_parsed_table
from src.engine.structured_rules import build_parsed_tables

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "sample_page_data.json"
_SAMPLE_PDFS = glob.glob(str(ROOT / "corpus" / "DOC-20260905-001" / "*.pdf"))

SOURCE_SHA = "113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7"


@pytest.fixture(scope="module")
def sample():
    """夹具 fail-closed：不存在即失败（不再静默跳过）。"""
    assert FIXTURE_PATH.exists(), (
        f"固定夹具缺失: {FIXTURE_PATH}——用 scripts/build_sample_fixture.py "
        "重新生成（GPT5.6 R4 P1-5：关键测试不允许静默跳过）"
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["source_pdf_sha256"] == SOURCE_SHA, "夹具源 PDF SHA 不符"
    if _SAMPLE_PDFS:
        # 本地有真实 PDF：交叉校验夹具未过期/未被篡改
        local_sha = hashlib.sha256(
            Path(_SAMPLE_PDFS[0]).read_bytes()
        ).hexdigest()
        assert local_sha == payload["source_pdf_sha256"], (
            "本地样张与夹具 SHA 不一致——夹具过期，请重新生成"
        )
    page_texts = payload["page_texts"]
    page_tables = payload["page_tables"]
    doc = build_document(
        path="DOC-20260905-001.pdf",
        page_texts=page_texts,
        page_tables=page_tables,
        filesize=0,
    )
    return doc, page_tables


def test_sample_table_boundaries_follow_page_text_anchors(sample):
    """表边界服从页文本表名锚：12 张逻辑表、跨表种合并为零。"""
    doc, page_tables = sample
    raw_count = sum(len(t) for t in page_tables)
    tables = build_parsed_tables(page_tables, doc.page_texts)

    assert raw_count == 14, f"样张原始表数量变化: {raw_count}"
    assert len(tables) == 12, (
        f"逻辑表 {len(tables)} 张：R4 表名锚语义基线是 12（10 表名 + 2 保守续页）"
    )
    raw_rows = sum(
        len(raw) for tables_on_page in page_tables for raw in tables_on_page
    )
    merged_rows = sum(len(t.rows) for t in tables.values())
    assert merged_rows >= raw_rows, "存在静默丢表"
    # 逐表对照页文本表名清单：跨表种合并必须为零
    by_anchor = {}
    for t in tables.values():
        if t.anchor_table_name:
            by_anchor.setdefault(t.anchor_table_name, []).append(t.page_span)
    expected = {
        "收入支出决算总表": [(7, 7)],          # R3 错误断言曾是 (7,8)
        "收入决算表": [(8, 8)],
        "支出决算表": [(10, 10)],
        "财政拨款收入支出决算总表": [(12, 13)],  # 真续表（P13 无新表名）
        "一般公共预算财政拨款支出决算表": [(14, 14)],
        "一般公共预算财政拨款基本支出决算表": [(15, 16)],  # 真续表
        "财政拨款“三公”经费支出决算表": [(17, 17)],
        "政府性基金预算财政拨款收入支出决算表": [(18, 18)],
        "国有资本经营预算财政拨款收入支出决算表": [(19, 19)],
    }
    for name, spans in expected.items():
        got = by_anchor.get(name)
        assert got == spans, f"表「{name}」span 应为 {spans}，实测 {got}"


def test_sample_v33_115_enters_structured_consumption_path(sample):
    """V33-115 命中总表 (7,7) 并进入结构化路径，取到真实总计 4733.14。"""
    from decimal import Decimal

    doc, page_tables = sample
    doc.parsed_tables = build_parsed_tables(page_tables, doc.page_texts)

    target = _find_parsed_table(doc, "收入支出决算总表")
    assert target is not None, "两级匹配（title→锚点页）未命中样张总表"
    # R4 修正：总表只占 P7（P8 起是收入决算表）
    assert target.page_span == (7, 7), f"总表 span 异常: {target.page_span}"
    assert len(target.rows) > 10, f"总表行数异常: {len(target.rows)}"
    totals = [
        r
        for r in target.rows
        if "总计" in r.label and r.row_role in ("total", "subtotal")
    ]
    assert totals, "总表必须解析出总计行"
    numbers = [c.number for c in totals[0].cells if c.number is not None]
    assert numbers == [Decimal("4733.14")] * 2, (
        f"总计两侧应为 4733.14: {numbers}"
    )
    # 平衡表 → 0 issues 是由数据支撑的正确结论
    issues = R33115_TotalSheetCheck().apply(doc)
    assert issues == [], f"平衡总表不应产出 finding: {[i.message for i in issues]}"


def test_sample_v33_115_structured_matches_legacy_conclusion(sample):
    """结构化与 legacy 在样张总表上结论一致（输入表征不同、语义相同）。"""
    doc, page_tables = sample
    legacy_issues = R33115_TotalSheetCheck().apply(doc)
    doc.parsed_tables = build_parsed_tables(page_tables, doc.page_texts)
    structured_issues = R33115_TotalSheetCheck().apply(doc)
    assert len(legacy_issues) == len(structured_issues)
