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
    materialize_table,
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


def test_materialize_multi_measure_empty_first_column_header():
    """首列表头为空的多金额组表不得崩溃（R7 P0-2 边界，历史材料实测）。

    历史回放里 3 份 final 全部 worker_failed（tuple index out of
    range）：列组切分对空标签列调 seq[-1]。修复后空列不建组、
    其余列组正常逐组保留主体。
    """
    raw = [
        ["", "合计", "", "公务接待费", ""],
        ["", "预算数", "决算数", "预算数", "决算数"],
        ["", "21.00", "16.95", "0.50", "0.00"],
    ]
    table = materialize_table(raw, title="t", table_code="T", pages=(1,))
    assert table.column_group == "multi_measure"
    assert table.semantic_columns["budget"] == [1, 3]
    assert table.semantic_columns["final"] == [2, 4]
    by_subject = {g.subject: g for g in table.column_groups}
    # 「合计」主体本身也是 total 语义键命中（与真实样张三公表一致）
    assert by_subject["合计"].columns == {"total": 1, "budget": 1, "final": 2}
    assert by_subject["公务接待费"].columns == {"budget": 3, "final": 4}


def test_remap_prefix_align_code_shaped_first_cell():
    """合并编码（7 位数字形态）前对齐到基准 code 列位（R6 P0-2 语义）。"""
    from src.engine.structured_rules import ParsedCell

    row = ParsedRow(
        cells=[
            ParsedCell(text="2110105"),
            ParsedCell(text="40.00"),
            ParsedCell(text="1.00"),
            ParsedCell(text="2.00"),
            ParsedCell(text="3.00"),
        ]
    )
    # 基准宽 6、code 列位 0：首列是编码形态 → 前对齐到 cells[0]
    remapped = _remap_continuation_rows([row], 6, code_column=0)
    cells = remapped[0].cells
    assert cells[0].text == "2110105", "合并编码应前对齐到 code 列位"
    # 其余列尾部对齐：原第 j 列（j≥1）到基准 j+1
    assert cells[2].text == "40.00"
    assert cells[5].text == "3.00"


def test_remap_prefix_align_rejects_sequence_number_first_cell():
    """首列为序号文本时不做前对齐（review 🟢3：code 列位不被污染）。"""
    from src.engine.structured_rules import ParsedCell

    row = ParsedRow(
        cells=[
            ParsedCell(text="1"),  # 序号，非 3/5/7 位编码形态
            ParsedCell(text="40.00"),
            ParsedCell(text="1.00"),
            ParsedCell(text="2.00"),
            ParsedCell(text="3.00"),
        ]
    )
    remapped = _remap_continuation_rows([row], 6, code_column=0)
    cells = remapped[0].cells
    assert cells[0].text in (None, ""), "序号文本不得进 code 列位"
    # 全部列走尾部对齐：原第 0 列落到基准第 1 列
    assert cells[1].text == "1"
    assert cells[5].text == "3.00"


def test_remap_wider_continuation_never_prefix_aligns():
    """续页比基准宽（负 shift）：不做前对齐，编码单元格不被覆盖。

    /review 自查边界：负 shift 时尾部循环 src=j+shift 可能落回 code
    列位——前对齐语义只适用于续页窄于基准（合并编码成单列），宽续页
    无此形态，必须保持纯尾部对齐。
    """
    from src.engine.structured_rules import ParsedCell

    row = ParsedRow(
        cells=[
            ParsedCell(text="211"),  # 编码形态但续页更宽——不前对齐
            ParsedCell(text="科目名称"),
            ParsedCell(text="40.00"),
            ParsedCell(text="1.00"),
            ParsedCell(text="2.00"),
        ]
    )
    remapped = _remap_continuation_rows([row], 4, code_column=0)
    cells = remapped[0].cells
    assert len(cells) == 4
    # 负 shift=-1：j=0 落 src=-1（丢弃），j=1..4 落 src=0..3
    assert cells[0].text == "科目名称", "负 shift 不得前对齐，尾部对齐原样"
    assert all(c.text != "211" for c in cells), "编码不应被特殊放置"


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
# build_parsed_tables 跨页续表合并（GPT5.6 R2→R4 语义演进）
#
# R2：key 含累计表数永不重复 → 续表从不合并。
# R3：内容签名+物理相邻——真实样张暴露跨表种误合并（14→4）。
# R4：**表名锚约束**——合并要求「基准表有表名锚 + 续页无新表名」，
# 页文本出现新表名行即物理翻表，无条件禁止合并；双方都无表名时保守
# 不合并（无业务身份证据）。测试因此需传 page_texts 提供表名行。
# ---------------------------------------------------------------------------


def _cont_page_tables():
    """两页续表原始数据 + 页文本（首页有表名行、续页无）。"""
    page1 = [[
        ["收入支出决算总表", "", "", ""],
        ["项目", "合计", "基本支出", "项目支出"],
        ["一、财政拨款", "100.00", "60.00", "40.00"],
    ]]
    page2 = [[
        ["二、事业收入", "20.00", "10.00", "10.00"],
        ["总计", "120.00", "70.00", "50.00"],
    ]]
    texts = ["收入支出决算总表\n单位：万元\n" + "x" * 40, "续页无表名行"]
    return [page1, page2], texts


def test_build_parsed_tables_merges_cross_page_continuation():
    page_tables, texts = _cont_page_tables()
    tables = build_parsed_tables(page_tables, texts)
    # 首页表名锚 + 续页无新表名 + 签名兼容 → 合并
    assert len(tables) == 1, f"续表未合并: keys={sorted(tables)}"
    merged = next(iter(tables.values()))
    assert merged.page_span == (1, 2)
    assert merged.anchor_table_name == "收入支出决算总表"


def test_build_parsed_tables_new_anchor_blocks_merge():
    """相邻页出现新表名行（物理翻表）：无条件禁止合并（R4 核心语义）。"""
    page1 = [[
        ["收入支出决算总表", "", "", ""],
        ["项目", "合计", "基本支出", "项目支出"],
    ]]
    page2 = [[
        ["收入决算表", "", ""],
        ["项目", "合计", "基本支出", "项目支出"],
    ]]
    # 两页页文本各含自己的表名行（P7 总表 → P8 收入决算表的真实形态）
    texts = ["收入支出决算总表", "收入决算表"]
    tables = build_parsed_tables([page1, page2], texts)
    assert len(tables) == 2, "相邻页不同表种必须保持独立"
    spans = sorted(t.page_span for t in tables.values())
    assert spans == [(1, 1), (2, 2)]


def test_build_parsed_tables_no_anchor_no_merge():
    """双方都无表名锚（页文本缺失/表名提取失败）：保守不合并（R4）。"""
    page1 = [[["项目", "合计"], ["行1", "1.00"]]]
    page2 = [[["行2", "2.00"]]]
    # 不传 page_texts → 无表名锚 → 无业务身份证据，宁可分不误并
    tables = build_parsed_tables([page1, page2])
    assert len(tables) == 2


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
    # 首页有表名行、P2/P3 是无表名的延续页（真实续页形态）
    texts = ["支出决算表\n单位：万元", "", ""]
    tables = build_parsed_tables([page1, page2, page3], texts)
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


# ---------------------------------------------------------------------------
# GPT5.6 R4 P1-2：金额与科目编码的区分
# ---------------------------------------------------------------------------


def test_amount_301_is_not_misread_as_code():
    """金额恰好为 3 位整数（"301"）不得判为科目编码（R4 P1-2）。"""
    from src.engine.structured_rules import materialize_table

    raw = [
        ["项目", "金额（万元）", "备注"],
        ["某支出事项", "301", "全额"],
    ]
    table = materialize_table(raw, title="测试表", pages=(1,))
    assert all(r.code is None for r in table.rows), (
        [r.code for r in table.rows]
    )


def test_code_column_number_form_is_recognized():
    """表头确认「科目编码」列时：number 形态的 301/30101 正确识别。"""
    from src.engine.structured_rules import materialize_table

    raw = [
        ["科目编码", "科目名称", "决算数"],
        ["301", "工资福利支出", "300.00"],
        ["30101", "基本工资", "200.00"],
    ]
    table = materialize_table(raw, title="测试表", pages=(1,))
    assert table.named_columns.get("code") == 0
    codes = [r.code for r in table.rows]
    assert codes == [None, "301", "30101"]


def test_build_parsed_tables_same_page_two_tables_never_merge():
    """同页两张同结构业务表不得误合并（R8 P2 复现防护）。

    同页「收入决算表」与「支出决算表」结构相同（项目/决算数）时，
    此前同页复用同一表名锚 + 签名兼容 → 误并为 1 张（实测 table_count=1）。
    同页第 2 张 raw table 无 bbox 连续性证据，默认独立成表。
    """
    raw = [
        ["项目", "决算数"],
        ["类", "款", "项", "合计"],
        ["208", "", "", "471.44"],
    ]
    tables = build_parsed_tables(
        [[raw, raw]], ["收入决算表\n支出决算表\n"]
    )
    assert len(tables) == 2, f"同页两表应独立: {len(tables)}"


def test_build_parsed_tables_continuation_plus_new_table_on_same_page():
    """混合页面（页顶续表 + 下方新表）不得拆断续表（R9 P1-4 反例）。

    此前页文本的首个表名行（属新表）被安到页顶第一张 raw table——
    续表错标新表名、拒绝合并，应 2 张实得 3 张。修复后：多表页不赋
    页锚，首表无表头（续表形态）仍可并入基准表。
    """
    base = [
        ["功能分类科目编码", "科目名称", "决算数"],
        ["类", "款", "项", "合计"],
        ["208", "", "", "471.44"],
    ]
    cont = [
        ["20805", "", "", "460.67"],
        ["2080501", "", "", "51.98"],
    ]
    new_table = [
        ["项目", "决算数"],
        ["类", "款", "项", "合计"],
        ["211", "", "", "3742.47"],
    ]
    tables = build_parsed_tables(
        [[base], [cont, new_table]],
        ["支出决算表\n", "支出决算表\n"],
    )
    assert len(tables) == 2, (
        f"续表应并入基准、新表独立（应 2 张）: {len(tables)}"
    )
    merged = next(
        (t for t in tables.values() if t.page_span == (1, 2)), None
    )
    assert merged is not None, "续表必须并入基准表 (1,2)"
    assert merged.anchor_table_name == "支出决算表"
    cont_rows = [str(c.number or c.text or "") for r in merged.rows for c in r.cells]
    assert any("2080501" in v for v in cont_rows), "续页明细行不得丢失"


def test_build_parsed_tables_continuation_with_total_data_row_merges():
    """页顶续表首行即「合计」数据行时仍须并入基准表（/review R9 自查反例）。

    R9 P1-4 修复的残留形态：续表扫描窗口（表头+前 2 数据行）内含
    精确「合计」**数据行**（含金额）——命中被登记为表头语义列后，
    多表页首表守卫（named_columns 非空 → 不可续表）与签名守卫
    （仅共 total 单键 → 表头文本复核失败）双双拒并，应 2 张实得
    3 张。修复后：含金额数字的扫描行是数据行，其命中不作表头证据。
    """
    base = [
        ["支出决算表", "", ""],
        ["科目", "合计", "基本支出"],
        ["201", "100", "80"],
    ]
    cont = [["合计", "120", "90"]]  # 页顶续表：首行即合计数据行（无表头）
    new_table = [
        ["收入决算表", "", ""],
        ["科目", "合计", ""],
        ["101", "50", ""],
    ]
    tables = build_parsed_tables(
        [[base], [cont, new_table]],
        ["支出决算表\n", "收入决算表\n"],
    )
    assert len(tables) == 2, (
        f"含合计数据行的续表应并入基准、新表独立（应 2 张）: {len(tables)}"
    )
    merged = next(
        (t for t in tables.values() if t.page_span == (1, 2)), None
    )
    assert merged is not None, "续表必须并入基准表 (1,2)"
    assert merged.anchor_table_name == "支出决算表"
    assert len(merged.rows) == 4, f"合并后应 4 行: {len(merged.rows)}"
    total_cells = [str(c.number or "") for c in merged.rows[-1].cells]
    assert "120" in total_cells, "续表合计数据行不得丢失"


def test_materialize_total_data_row_not_registered_as_header():
    """数据行的精确「合计」命中不得登记为表头语义列（/review R9 自查）。

    普通单金额表的合计数据行（含金额）在扫描窗口内——其命中此前
    进入 semantic_columns（total=[1,0] 跨行列位混入），过滤后只保
    留表头行命中（total=[1]）。
    """
    raw = [
        ["项目", "合计", "基本支出"],
        ["类", "款", "项", ""],
        ["合计", "4,733.14", "3,365.38"],
    ]
    table = materialize_table(raw, title="t", table_code="T", pages=(1,))
    assert table.semantic_columns.get("total") == [1], (
        f"合计数据行（含金额）不得登记为表头语义列: "
        f"{table.semantic_columns.get('total')}"
    )
    assert table.named_columns.get("total") == 1
    assert table.column_group != "multi_measure"


def test_multi_measure_same_column_repeats_not_triggered():
    """同列跨行重复命中不得误触发 multi_measure（R9 P2 反例）。

    普通单金额表的「合计」在表头与首条数据行同列重复出现——此前按
    命中次数判定误标 multi_measure；修复后按同行不同列位置判定。
    """
    raw = [
        ["项目", "合计"],
        ["类", "款", "项", "合计"],
        ["208", "", "", "471.44"],
    ]
    table = materialize_table(raw, title="t", table_code="T", pages=(1,))
    assert table.column_group != "multi_measure", (
        f"同列跨行重复不得触发 multi_measure: {table.column_group}"
    )
    # 「合计」在表头行第 1 列与数据行第 3 列各命中一次（跨行），语义
    # 位置如实记录；multi_measure 判定不因跨行重复而触发
    assert table.semantic_columns.get("total") == [1, 3]
    assert table.named_columns.get("total") == 1
