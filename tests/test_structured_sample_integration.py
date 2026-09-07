"""官方样张结构化解析集成测试（GPT5.6 R4 P1-5：fail-closed，CI 可复现）。

R3 版问题：corpus/ 被 .gitignore 排除，干净 checkout 无样张 PDF，
skipif 让三项关键测试在 CI 上静默跳过。R4 修复：解析产物
（page_texts/page_tables，公开决算材料）序列化为入库夹具
（tests/fixtures/sample_page_data.json，含源 PDF SHA-256 溯源），
测试基于夹具 fail-closed 运行；本地有真实 PDF 时做 SHA 交叉校验
（夹具与 PDF 不一致即失败——防止夹具过期或被篡改）。

锁定的业务基线（R5 终版，样张实测）：
- 10 张逻辑表 = 9 个页文本表名各归各位 + P6 机构表（无表名辅助表）；
- 全部真实续页正确合并：P8→9、P10→11（无表头窄续页，经显式尾部
  重映射并入）、P12→13、P15→16；P9/P11 的分类明细行必须可被规则
  取到（V33-117/120 迁移的输入完整性前提）；
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
# 夹具内容哈希锁定（GPT5.6 R5 P1-D）：源 PDF SHA 只能溯源不能检测夹具
# 本体被修改——内容哈希与源 SHA 双锁，任一不符即失败。
FIXTURE_SHA = "a85b158735413ee52483d4138e00c4fe34f1a3621ea2fb1727b2e647f7524c16"


@pytest.fixture(scope="module")
def sample():
    """夹具 fail-closed：不存在即失败（不再静默跳过）。"""
    assert FIXTURE_PATH.exists(), (
        f"固定夹具缺失: {FIXTURE_PATH}——用 scripts/build_sample_fixture.py "
        "重新生成（GPT5.6 R4 P1-5：关键测试不允许静默跳过）"
    )
    fixture_sha = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert fixture_sha == FIXTURE_SHA, (
        "夹具内容哈希不符——夹具被修改或需按新解析逻辑重新生成"
        "（scripts/build_sample_fixture.py），并同步更新 FIXTURE_SHA"
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
    """表边界服从页文本表名锚：10 张逻辑表（R5 P0-A 基线）。

    R4 版把 P9/P11 的「保守独立」固化成 12 张基线——GPT5.6 R5 指出
    这两张是**真实续页**（P9 是收入决算表续页、P11 是支出决算表续页，
    无表头行、行宽 9/8 vs 基准 11/10，列宽族不重叠被 merge 守卫误拒），
    其 14/16 行分类明细数据因此不进 V33-120。R5 修复后续页经显式
    尾部重映射并入基准表。正确基线：10 张 = 9 个表名 + P6 机构表
    （无表名的辅助表）。
    """
    doc, page_tables = sample
    raw_count = sum(len(t) for t in page_tables)
    tables = build_parsed_tables(page_tables, doc.page_texts)

    assert raw_count == 14, f"样张原始表数量变化: {raw_count}"
    assert len(tables) == 10, (
        f"逻辑表 {len(tables)} 张：R5 基线是 10（P9/P11 续页须并入各自基准表）"
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
        "收入支出决算总表": [(7, 7)],
        "收入决算表": [(8, 9)],           # R5：P9 续页并入（R4 曾错误独立）
        "支出决算表": [(10, 11)],         # R5：P11 续页并入（R4 曾错误独立）
        "财政拨款收入支出决算总表": [(12, 13)],
        "一般公共预算财政拨款支出决算表": [(14, 14)],
        "一般公共预算财政拨款基本支出决算表": [(15, 16)],
        "财政拨款“三公”经费支出决算表": [(17, 17)],
        "政府性基金预算财政拨款收入支出决算表": [(18, 18)],
        "国有资本经营预算财政拨款收入支出决算表": [(19, 19)],
    }
    for name, spans in expected.items():
        got = by_anchor.get(name)
        assert got == spans, f"表「{name}」span 应为 {spans}，实测 {got}"


def test_sample_continuation_rows_are_reachable_by_rules(sample):
    """续页明细数据必须进入合并表（R5 P0-A 的核心验收）。

    P9 首行「2110105 环境保护法规…7.50」与 P11 首行「2110101 行政
    运行 1,293.60」——此前断裂时这些行不进 V33-120 的层级校验。
    合并后从 ParsedTable 命名行可取到这些明细（V33-117/120 迁移的
    输入完整性前提）。
    """
    doc, page_tables = sample
    tables = build_parsed_tables(page_tables, doc.page_texts)
    income = next(
        (t for t in tables.values() if t.anchor_table_name == "收入决算表"), None
    )
    expense = next(
        (t for t in tables.values() if t.anchor_table_name == "支出决算表"), None
    )
    assert income is not None and expense is not None
    # P9 明细（编码 2110105 是 number 形态，不在 text label 中）在
    # 收入决算表合并体的行单元格里可取到
    income_cells = [str(c.number or c.text or "") for r in income.rows for c in r.cells]
    assert any("2110105" in v for v in income_cells), (
        "P9 续页明细未进入收入决算表"
    )
    # P11 明细（编码 2110101）在支出决算表合并体中
    expense_cells = [str(c.number or c.text or "") for r in expense.rows for c in r.cells]
    assert any("2110101" in v for v in expense_cells), (
        "P11 续页明细未进入支出决算表"
    )
    # 合并行的 page 归属正确（P9 的行 page=9、P11 的行 page=11）
    pages_9 = {c.page for r in income.rows for c in r.cells if c.page == 9}
    assert pages_9 == {9}, "收入决算表合并体应含 P9 行"


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


def test_sample_two_sided_right_lane_is_preserved(sample):
    """双栏右栏数据规范化（R5 P0-B）：右栏编码/金额必须可取。

    样张 P15-16 基本支出表双栏 8 列 [类,款,名称,金额 × 2]：
    此前单值 code/named_columns 只记录左栏（code=0、final=3），
    右栏 310（第 4 列）与金额（第 7 列）全部丢失——V33-117 结构化
    迁移最关键的 T4 真值（310 行 14.44 vs 明细和 14.43）不可达。
    R5 后右栏语义列记 *_right、行编码存 code_right。
    """
    from decimal import Decimal

    doc, page_tables = sample
    tables = build_parsed_tables(page_tables, doc.page_texts)
    basic = next(
        t for t in tables.values()
        if t.anchor_table_name and "基本支出" in t.anchor_table_name
    )
    assert basic.column_group == "two_sided"
    assert basic.named_columns.get("final_right") == 7
    assert basic.named_columns.get("code_right") == 4
    # 右栏 310 行：code_right=310、金额 14.44 可从 final_right 列取到
    rows_310 = [
        r for r in basic.rows
        if r.code_right and r.code_right.startswith("310")
    ]
    assert rows_310, "右栏 310 行不可达——V33-117 T4 真值缺失"
    amounts = [
        r.cells[basic.named_columns["final_right"]].number
        for r in rows_310
        if r.cells[basic.named_columns["final_right"]].number is not None
    ]
    assert Decimal("14.44") in amounts, f"右栏 310 的 14.44 未取到: {amounts}"
