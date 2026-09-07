"""structured_rules 跨页合并守卫测试（GPT5.6 P0-3 整改）。

背景：merge_compatible 此前在列宽不一致时只记 parse_error 就直接
extend 合并（错位合而不重映射），且 structured_ready 以"迁移列表非空"
判定、对 9/52 的覆盖率误报可切换。本文件锁定合并守卫的三个契约：
1. 有共同语义列 + 列宽漂移 → 显式尾部对齐重映射后合并；
2. 表头/语义列不兼容 → 拒绝合并，续表独立；
3. 重映射/拒绝均留 parse_errors 供质量门与评测消费。
"""

import pytest

from src.engine.structured_rules import (
    ParsedRow,
    ParsedTable,
    _remap_continuation_rows,
    build_parsed_tables,
    merge_compatible,
)


def _table(named_columns, rows_cells, width_hint=None):
    table = ParsedTable(table_code="T1", title="测试表", named_columns=dict(named_columns))
    for cells in rows_cells:
        table.rows.append(ParsedRow(cells=[_cell(c) for c in cells]))
    return table


def _cell(value):
    from src.engine.structured_rules import ParsedCell

    return ParsedCell(text=value)

def test_remap_continuation_rows_aligns_tail_columns():
    """宽 4 的续页行重映射到宽 6：尾部金额列按行宽差对齐，前置列独立。"""
    row = ParsedRow(cells=[_cell("301"), _cell("工资福利"), _cell("40.00"), _cell("1.00")])
    remapped = _remap_continuation_rows([row], 6)
    assert len(remapped[0].cells) == 6
    # 尾部对齐：宽 4 的 j 映射到 6 的 j+2，末列必须是原行最后一列
    assert remapped[0].cells[5].text == "1.00"
    assert remapped[0].cells[4].text == "40.00"
    # 前置填充列不应携带金额
    assert remapped[0].cells[0].text in (None, "")


def test_merge_same_width_appends_without_remap():
    base = _table({"合计": 2}, [["合计", "10.00", "x"]])
    cont = _table({"合计": 2}, [["明细", "1.00", "y"]])
    assert merge_compatible(base, cont) is True
    assert len(base.rows) == 2
    assert base.parse_errors == []


def test_merge_width_drift_with_shared_columns_remaps_then_merges():
    """列宽漂移 + 有共同语义列：重映射后合并，并留 continuation_width_remapped。"""
    base = _table({"合计": 3, "基本支出": 4}, [["类", "款", "合计", "基本支出"]])
    cont = _table({"合计": 2, "基本支出": 3}, [["款", "合计", "基本支出"]])
    assert merge_compatible(base, cont) is True
    assert any("continuation_width_remapped" in e for e in base.parse_errors)
    # 合并后的行宽统一到基准宽度
    assert len(base.rows[1].cells) == len(base.rows[0].cells)


def test_merge_rejects_incompatible_headers():
    """表头语义列不相交：拒绝合并，留 continuation_header_incompatible。"""
    base = _table({"合计": 2}, [["合计", "10.00"]])
    cont = _table({"预算数": 1}, [["预算数", "5.00"]])
    assert merge_compatible(base, cont) is False
    assert any("continuation_header_incompatible" in e for e in base.parse_errors)
    assert len(base.rows) == 1


def test_merge_rejects_width_drift_without_shared_columns():
    """列宽漂移且无共同语义列（宽差无法可靠对齐）：拒绝合并。

    场景：base 与 continuation 的 named_columns 为空（表头未识别出
    语义列），列宽族不重叠 → width 分支因无共享列拒绝，续表独立。
    """
    base = _table({}, [["类", "合计", "10.00"]])  # 宽 3
    cont = _table({}, ["合计", "1.00"])  # 宽 2
    cont.rows = [ParsedRow(cells=[_cell("合计"), _cell("1.00")])]
    assert merge_compatible(base, cont) is False
    assert any(
        "continuation_width_incompatible_no_shared_columns" in e
        for e in base.parse_errors
    )
    assert len(base.rows) == 1


def test_empty_continuation_merges_trivially():
    base = _table({"合计": 1}, [["合计", "1.00"]])
    cont = ParsedTable(table_code="T1-cont", named_columns={"合计": 0})
    assert merge_compatible(base, cont) is True
    assert base.parse_errors == []


# ---------------------------------------------------------------------------
# build_parsed_tables 跨页续表合并（GPT5.6 R2 P0-1a）
#
# 缺陷背景：原内联实现 key=f"P{页}:{累计表数}"，累计数只增不减 →
# key 永不重复 → 续表从不合并（实测 keys=['P1:0','P2:1']、merge_calls=0）。
# 修复后按表头签名（语义列交集/表头文本）识别续表并走 merge_compatible。
# ---------------------------------------------------------------------------


def _cont_page_tables():
    """两页同表头（语义列相同）的续表原始数据——GPT5.6 的动态验证场景。"""
    page1 = [[
        ["收入支出决算总表", "", "", ""],
        ["项目", "合计", "基本支出", "项目支出"],
        ["一、财政拨款", "100.00", "60.00", "40.00"],
    ]]
    page2 = [[
        ["二、事业收入", "20.00", "10.00", "10.00"],
        ["总计", "120.00", "70.00", "50.00"],
    ]]
    return [page1, page2]


def test_build_parsed_tables_merges_cross_page_continuation():
    tables = build_parsed_tables(_cont_page_tables())
    # 语义列相同（total/basic/project）→ 第二页识别为续表合并进第一张
    assert len(tables) == 1, f"续表未合并: keys={sorted(tables)}"
    merged = next(iter(tables.values()))
    assert len(merged.rows) == 5  # 3 + 2，表头行也随行保留
    assert merged.page_span == (1, 2)


def test_build_parsed_tables_keeps_disjoint_tables_separate():
    """语义列零交集的两张表（不同表种）：保持独立，不误合并。"""
    page1 = [[
        ["收入决算表", "", ""],
        ["项目", "合计", "本年收入"],
    ]]
    page2 = [[
        ["支出决算表", "", ""],
        ["功能分类", "本年支出合计", "决算数"],
    ]]
    tables = build_parsed_tables([page1, page2])
    assert len(tables) == 2
    # 两张表语义列不同（本年收入 vs 决算数），不构成续表


def test_build_parsed_tables_chains_multi_page_continuation():
    """三页链式续表：P2 合并进 P1 后，P3 仍可与合并后的表继续合并。"""
    page1 = [[["项目", "合计", "基本支出", "项目支出"], ["一、行1", "1.00", "", ""]]]
    page2 = [[["二、行2", "2.00", "", ""]]]
    page3 = [[["三、行3", "3.00", "", ""]]]
    tables = build_parsed_tables([page1, page2, page3])
    assert len(tables) == 1, f"链式续页未全合并: keys={sorted(tables)}"
    merged = next(iter(tables.values()))
    assert merged.page_span == (1, 3)


# ---------------------------------------------------------------------------
# V33-115 结构化消费试点（GPT5.6 R2 P0-1b）
#
# doc 挂载 parsed_tables 后，规则必须真正消费 ParsedTable（而非仅构建
# 后仍走 legacy 文本行）——金额取自与「总计」标签同侧半行的
# ParsedCell.number，ParsedCell 三态保证文本单元格不会误读成 0.0。
# ---------------------------------------------------------------------------


def _total_sheet_parsed_table(income_total="120.00", expense_total="120.00",
                              income_parts=None, expense_parts=None):
    """构造收入支出决算总表的 ParsedTable（双栏 4 列布局）。"""
    from src.engine.structured_rules import ParsedCell

    rows = [
        ParsedRow(
            cells=[
                ParsedCell(text="收入支出决算总表"), ParsedCell(),
                ParsedCell(), ParsedCell(),
            ],
            row_role="header",
        ),
        ParsedRow(
            cells=[
                ParsedCell(text="收入总计"), ParsedCell(number=__import__("decimal").Decimal(income_total)),
                ParsedCell(text="支出总计"), ParsedCell(number=__import__("decimal").Decimal(expense_total)),
            ],
            row_role="total",
        ),
    ]
    for label, value in (income_parts or []):
        rows.append(ParsedRow(cells=[
            ParsedCell(text=label), ParsedCell(number=__import__("decimal").Decimal(value)),
            ParsedCell(text=label.replace("收入", "支出").replace("结余", "分配")),
            ParsedCell(number=__import__("decimal").Decimal(value)),
        ]))
    table = ParsedTable(
        table_code="FIN_01_total",
        title="收入支出决算总表",
        named_columns={"total": 1},
        column_group="two_sided",
        page_span=(2, 2),
    )
    table.rows.extend(rows)
    return table


def test_v33_115_consumes_parsed_tables_when_mounted():
    """挂载结构化表后：平衡错误必须来自 ParsedCell.number 命中（高区分度证据）。"""
    from decimal import Decimal

    from src.engine.rules_v33 import R33115_TotalSheetCheck, build_document

    table = _total_sheet_parsed_table(income_total="120.00", expense_total="121.00")
    doc = build_document(path="x决算.pdf", page_texts=[], page_tables=[], filesize=0)
    doc.parsed_tables = {"P1:0": table}

    issues = R33115_TotalSheetCheck().apply(doc)
    assert issues, "结构化路径未产出平衡错误 finding"
    assert any("收入总计" in i.message and "支出总计" in i.message for i in issues)
    assert any("120.00" in (i.evidence_text or "") and "121.00" in (i.evidence_text or "") for i in issues)


def test_v33_115_structured_balanced_table_yields_no_issues():
    """平衡表：结构化路径零 finding（无误报）。"""
    from src.engine.rules_v33 import R33115_TotalSheetCheck, build_document

    table = _total_sheet_parsed_table()
    doc = build_document(path="x决算.pdf", page_texts=[], page_tables=[], filesize=0)
    doc.parsed_tables = {"P1:0": table}

    issues = R33115_TotalSheetCheck().apply(doc)
    assert issues == []


def test_v33_115_falls_back_to_legacy_when_not_mounted():
    """未挂载 parsed_tables：走 legacy 文本行路径（无表时抛 RuleDeferred，
    证明没有误入结构化路径；RuleDeferred 继承 BaseException，须显式捕获）。"""
    from src.engine.rule_outcome import RuleDeferred
    from src.engine.rules_v33 import R33115_TotalSheetCheck, build_document

    doc = build_document(path="x决算.pdf", page_texts=["决算"], page_tables=[], filesize=0)
    with pytest.raises(RuleDeferred):
        R33115_TotalSheetCheck().apply(doc)


def test_build_parsed_tables_same_page_disjoint_tables_stay_separate():
    """同页两张不同表种（语义列零交集）：必须保持独立，不误判续表。

    审查发现易混淆点：page_tables 的一页是"表格列表"（[t1, t2]），
    同页相邻的两张不同表也走 last_key 续表判定——语义列零交集
    （{total} vs {final}）且表头文本互不包含时必须拒绝合并。
    """
    t1 = [["收入决算表", "", ""], ["项目", "合计", "本年收入"]]
    t2 = [["支出决算表", "", ""], ["功能分类", "决算数", "备注"]]
    tables = build_parsed_tables([[t1, t2]])  # 一页两表
    assert len(tables) == 2, f"同页不同表种被误合并: keys={sorted(tables)}"
    titles = [t.title[:5] for t in tables.values()]
    assert "收入决算表" in "".join(titles) and "支出决算表" in "".join(titles)
