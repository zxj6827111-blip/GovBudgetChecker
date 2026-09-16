"""测试严格字段与数值提取基础设施（Contract C2 / 任务卡 B01 / 测试矩阵 T15、T18）。"""

from decimal import Decimal

from src.engine.field_extractor import (
    STATUS_BLANK,
    STATUS_INVALID,
    STATUS_MISSING_COLUMN,
    STATUS_OUT_OF_BOUNDS,
    STATUS_VALID,
    STATUS_ZERO,
    extract_row_strict,
    find_column_index,
    parse_strict_cell,
)


def test_t18_precision_and_unit_preservation():
    """T18: 原文100.00与100.0、元与万元，精度和单位保留且比较正确。"""
    # 1. 精度保留测试：100.00 保持两位精度，100.0 保持一位精度
    v_two = parse_strict_cell("100.00")
    assert v_two.status == STATUS_VALID
    assert v_two.decimal_val == Decimal("100.00")
    assert v_two.scale_digits == 2
    assert v_two.unit == "万元"

    v_one = parse_strict_cell("100.0")
    assert v_one.status == STATUS_VALID
    assert v_one.decimal_val == Decimal("100.0")
    assert v_one.scale_digits == 1

    v_zero_dec = parse_strict_cell("100")
    assert v_zero_dec.status == STATUS_VALID
    assert v_zero_dec.scale_digits == 0

    # 2. 单位识别与归一化测试：元与万元与亿元
    v_yuan = parse_strict_cell("10000元")
    assert v_yuan.status == STATUS_VALID
    assert v_yuan.unit == "元"
    assert v_yuan.raw_decimal == Decimal("10000")
    assert v_yuan.decimal_val == Decimal("1.0")  # 10000元 = 1万元

    v_yi = parse_strict_cell("1.5亿元")
    assert v_yi.status == STATUS_VALID
    assert v_yi.unit == "亿元"
    assert v_yi.decimal_val == Decimal("15000")  # 1.5亿元 = 15000万元

    v_wan = parse_strict_cell("25.50万元")
    assert v_wan.status == STATUS_VALID
    assert v_wan.unit == "万元"
    assert v_wan.decimal_val == Decimal("25.50")


def test_t15_zero_and_missing_differentiation():
    """T15: 三公/表格中缺列、越界、空白、无效文本、明确0，必须严格区分。"""
    # 1. 明确零
    z1 = parse_strict_cell("0")
    assert z1.status == STATUS_ZERO
    assert z1.is_zero is True
    assert z1.is_numeric is True
    assert z1.decimal_val == Decimal("0")

    z2 = parse_strict_cell("0.00")
    assert z2.status == STATUS_ZERO
    assert z2.scale_digits == 2
    assert z2.decimal_val == Decimal("0.00")

    # 横杠/斜杠占位符无明确约定视为未知空白（STATUS_BLANK，禁止当作数值0）
    for dash_char in ("-", "—", "–", "一", "/"):
        zd = parse_strict_cell(dash_char)
        assert zd.status == STATUS_BLANK
        assert zd.decimal_val is None
        assert zd.is_zero is False
        assert zd.is_missing is True

    # 带符号整数解析与单位换算
    neg_yuan = parse_strict_cell("-1000", default_unit="元")
    assert neg_yuan.status == STATUS_VALID
    assert neg_yuan.raw_decimal == Decimal("-1000")
    assert neg_yuan.decimal_val == Decimal("-0.1")  # -1000元 = -0.1万元

    # 非有限数值拒绝（NaN, Infinity）
    nan_val = parse_strict_cell("NaN")
    assert nan_val.status == STATUS_INVALID
    assert nan_val.decimal_val is None

    inf_val = parse_strict_cell("Infinity")
    assert inf_val.status == STATUS_INVALID
    assert inf_val.decimal_val is None

    # 2. 空白单元格
    b1 = parse_strict_cell("")
    assert b1.status == STATUS_BLANK
    assert b1.is_missing is True
    assert b1.decimal_val is None

    b2 = parse_strict_cell("   ")
    assert b2.status == STATUS_BLANK
    assert b2.is_missing is True

    bn = parse_strict_cell(None)
    assert bn.status == STATUS_BLANK

    # 3. 无效文本
    inv = parse_strict_cell("不适用文本XYZ")
    assert inv.status == STATUS_INVALID
    assert inv.is_missing is True
    assert "无法解析" in str(inv.reason)

    # 4. 行提取测试：缺列与越界
    row = ["合计", "100.00", "0.00"]
    # 缺列（col_idx 为 None）
    m_col = extract_row_strict(row, None, "项目支出")
    assert m_col.status == STATUS_MISSING_COLUMN
    assert m_col.is_missing is True
    assert "未定位到列" in str(m_col.reason)

    # 越界（col_idx >= len(row)）
    oob = extract_row_strict(row, 5, "越界列")
    assert oob.status == STATUS_OUT_OF_BOUNDS
    assert oob.is_missing is True
    assert "越界" in str(oob.reason)

    # 正常提取
    ok_val = extract_row_strict(row, 1, "合计")
    assert ok_val.status == STATUS_VALID
    assert ok_val.decimal_val == Decimal("100.00")


def test_column_disambiguation():
    """表头定位与消歧测试。"""
    headers = ["序号", "科目名称", "合计", "基本支出", "项目支出", "其中：人员经费"]

    # 精确匹配
    idx_total = find_column_index(headers, ["合计"])
    assert idx_total == 2

    idx_basic = find_column_index(headers, ["基本支出"])
    assert idx_basic == 3

    idx_proj = find_column_index(headers, ["项目支出"])
    assert idx_proj == 4

    # 排除词
    headers_sub = ["公务用车购置及运行费合计", "公务用车购置费", "公务用车运行费"]
    idx_run = find_column_index(headers_sub, ["运行费"])
    assert idx_run == 2

    idx_sub = find_column_index(headers_sub, ["公务用车购置及运行费", "小计"])
    assert idx_sub == 0

    # 重名歧义列：存在多个完全相等列名时返回 None，不猜列
    dup_headers = ["基本支出", "基本支出"]
    assert find_column_index(dup_headers, ["基本支出"]) is None

    # 多个候选且无法唯一最短消歧时返回 None
    ambig_headers = ["基本支出A", "基本支出B"]
    assert find_column_index(ambig_headers, ["基本支出"]) is None
