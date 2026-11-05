"""R5-01 列身份维度自控：预算/决算、基本/项目、收入来源、合计口径分别核验。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dec = load("dec_r6", HERE / "decimal_checks_v2.py")


def _value(name: str, **extra) -> dict:
    payload = {"name": name, "page": 1, "quote": "100.00", "unit": "万元"}
    payload.update(extra)
    return payload


def test_period_conflict_in_shared_expenditure_word_rejected():
    # “基本支出”共享词不能掩盖“预算 vs 决算表”的口径冲突。
    assert not dec._column_identity_ok(
        "基本支出预算数", _value("2210201 基本支出"),
        "一般公共预算财政拨款支出决算表")


def test_opposite_income_source_rejected():
    # “财政拨款收入”是“非财政拨款收入”的子串，但两者列身份相反。
    assert not dec._column_identity_ok(
        "非财政拨款收入", _value("财政拨款收入"), "收入决算表")


def test_expenditure_conflict_rejected():
    assert not dec._column_identity_ok(
        "项目支出决算数", _value("2210201 基本支出"),
        "一般公共预算财政拨款支出决算表")


def test_source_with_period_conflict_rejected():
    assert not dec._column_identity_ok(
        "事业收入预算数", _value("事业收入"), "收入决算表")


def test_unknown_column_cannot_default_pass():
    assert not dec._column_identity_ok("备注栏", _value("合计"), "收入决算表")


def test_table_proven_period_alias_positive():
    # 值名是纯行标签时，由“基本支出决算表”证明“决算数”列存在。
    assert dec._column_identity_ok(
        "决算数", _value("人员经费合计"),
        "一般公共预算财政拨款基本支出决算表")


def test_total_column_alias_positive():
    assert dec._column_identity_ok("本年收入合计", _value("合计"), "收入决算表")


def test_dual_period_table_budget_column_positive():
    # 三公表同时有预算数/决算数两列；值名自带“预算数”时应按值名放行。
    assert dec._column_identity_ok(
        "预算数", _value("合计 预算数"),
        "一般公共预算财政拨款“三公”经费支出决算表")
    assert dec._column_identity_ok(
        "决算数", _value("合计决算数"),
        "一般公共预算财政拨款“三公”经费支出决算表")


def test_explicit_column_conflict_and_positive():
    assert not dec._column_identity_ok(
        "预算", _value("", column="决算"), "收入决算表")
    assert dec._column_identity_ok(
        "决算", _value("", column="决算"), "收入决算表")
