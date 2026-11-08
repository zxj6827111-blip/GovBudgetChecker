import json
from decimal import Decimal

from src.engine.cell_value_policy import interpret_cell_value


IDENTITY = dict(region="上海市甲中心", year=2025, table_code="FIN_02_income", row=3, column=4)


def test_unknown_blank_dash_and_not_applicable_never_become_zero(monkeypatch):
    # 关闭约定时空白一律未知；默认 U-5 约定见 test_blank_zero_default_20261108。
    monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", "off")
    for raw in ("", "—", "/", "不适用", "无", "abc"):
        assert interpret_cell_value(raw, native_blank_verified=True, **IDENTITY)[0] is None
    assert interpret_cell_value("0.00", **IDENTITY)[:2] == (Decimal("0"), "zero")
    assert interpret_cell_value(0, **IDENTITY)[:2] == (Decimal("0"), "zero")


def test_scoped_test_convention_does_not_extend_to_other_year_or_unverified_ink(tmp_path, monkeypatch):
    path = tmp_path / "conventions.json"
    policy = dict(id="test-only-blank", source_kind="isolated_test", region="上海市", years=[2025],
                  table_codes=["FIN_02_income"], cell_ranges=[dict(rows=[3, 3], columns=[4, 4])], semantics={"blank": "zero"})
    path.write_text(json.dumps([policy]))
    monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", str(path))
    value, presence, basis = interpret_cell_value("", native_blank_verified=True, **IDENTITY)
    assert (value, presence, basis["id"]) == (Decimal("0"), "blank", "test-only-blank")
    assert interpret_cell_value("", native_blank_verified=False, **IDENTITY)[0] is None
    assert interpret_cell_value("—", native_blank_verified=True, **IDENTITY)[0] is None
    assert interpret_cell_value("", native_blank_verified=True, **{**IDENTITY, "year": 2024})[0] is None
    monkeypatch.setenv("TESTING", "false")
    assert interpret_cell_value("", native_blank_verified=True, **IDENTITY)[0] is None
