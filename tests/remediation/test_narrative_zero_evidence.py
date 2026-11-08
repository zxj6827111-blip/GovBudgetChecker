"""叙述性显式零证据：说明写明“该项决算为 0”时才为同身份空表格作证。

真实来源（2026-11-04 复审 RF-02b，第 5 份军粮中心 2025 决算）：
p15 表 overseas / 购置 / 接待的决算格是空白；p22 明确写“因公出国（境）费支出
决算 0 万元，占 0%”“公务用车购置支出为 0 万元”“公务接待费支出 0 万元”。
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.native_empty_operands import blank_term
from src.engine.narrative_zero_evidence import narrative_zero_sources

_FIXTURE = Path(__file__).resolve().parent / "test_native_blank_cells.py"


def _grid():
    spec = importlib.util.spec_from_file_location("_blank_fixture", _FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.grid_document


grid_document = _grid()


def _rows():
    return [
        ['财政拨款“三公”经费'] + [None] * 11,
        ['合计', None, '因公出国（境）费', None, '公务用车购置及运行维护费'] + [None] * 5 + ['公务接待费', None],
        [None] * 4 + ['小计', None, '公务用车购置费', None, '公务用车运行维护费', None, None, None],
        ['预算数', '决算数'] * 6,
        # 合计 11.20/0.56、出国 6.00/(空)、小计 3.20/0.56、购置 (空)/(空)、运行 3.20/0.56、接待 2.00/(空)
        ['11.20', '0.56', '6.00', '', '3.20', '0.56', '', '', '3.20', '0.56', '2.00', ''],
    ]


REAL_TEXT = (
    '甲局2024年度部门决算\n'
    '七、财政拨款“三公”经费支出决算情况说明\n'
    '“三公”经费财政拨款支出决算数比2023年度减少5.95万元，下降91.40%，其中：'
    '因公出国（境）费支出决算减少6.00万元，下降100%；公务接待费支出决算减少0.08万元，下降100%。\n'
    '（二）“三公”经费财政拨款支出决算具体情况说明。\n'
    '“三公”经费财政拨款支出决算中，因公出国（境）费支出决算0万元，占0%；'
    '公务用车购置及运行维护费支出决算0.56万元，占100%；公务接待费支出决算0万元，占0%。\n'
    '1、因公出国（境）费支出0万元。当年度根据实际工作情况，未安排因公出国。\n'
    '2、公务用车购置及运行维护费支出0.56万元。其中：公务用车购置支出为0万元。\n'
    '3、公务接待费支出0万元。当年度根据实际工作情况，未安排公务接待。'
)


def _doc(text=REAL_TEXT):
    return grid_document(_rows(), code='FIN_07_three_public', headers=4, text=text)


def test_real_order5_text_proves_final_zero_for_overseas_and_reception():
    doc = _doc()
    for indicator in ('overseas', 'reception', 'vehicle_purchase'):
        refs = narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                      indicator=indicator, stage='决算')
        assert refs, indicator
        assert all(ref.get('page') for ref in refs)
        assert any('0' in ref.get('span', '') for ref in refs)


def test_final_zero_never_fills_budget_column():
    doc = _doc()
    assert narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                  indicator='vehicle_purchase', stage='年初预算') is None


def test_nonzero_level_statement_blocks_the_zero_evidence():
    text = REAL_TEXT.replace('公务接待费支出0万元。当年度根据实际工作情况，未安排公务接待。',
                             '公务接待费支出0.08万元。')
    doc = _doc(text)
    assert narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                  indicator='reception', stage='决算') is None


def test_foreign_year_or_owner_is_rejected():
    text = REAL_TEXT.replace('因公出国（境）费支出0万元。', '2023年度因公出国（境）费支出0万元。')
    doc = _doc(text)
    assert narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                  indicator='overseas', stage='决算') is None


def test_blank_term_uses_narrative_zero_but_keeps_cell_blank(monkeypatch):
    # 本用例检验“无空白值约定时只认说明零披露”的路径；U-5 默认约定（2026-11-08）下认证空白直接为 0。
    monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", "off")
    doc = _doc()
    term = blank_term(doc, 'FIN_07_three_public', 'overseas', code='total', stage='决算')
    assert term is not None
    observation = term.extra_dims['native_blank_observation']
    assert observation['convention'] == 'narrative_explicit_zero'
    assert observation['raw_text'] == ''
    assert observation['narrative_sources']
    assert str(term.normalized_value) == '0'


def test_budget_stage_blank_stays_unknown(monkeypatch):
    # 本用例检验“无空白值约定时只认说明零披露”的路径；U-5 默认约定（2026-11-08）下认证空白直接为 0。
    monkeypatch.setenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", "off")
    doc = _doc()
    assert blank_term(doc, 'FIN_07_three_public', 'vehicle_purchase', code='total',
                      stage='年初预算') is None


def test_non_fiscal_zero_cannot_prove_fiscal_blank():
    """非财政拨款的零不能补财政拨款三公表的空格。"""
    doc = _doc(REAL_TEXT.replace(
        '公务接待费支出0万元。当年度根据实际工作情况，未安排公务接待。',
        '非财政拨款公务接待费支出决算0万元。'))
    assert narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                  indicator='reception', stage='决算') is None


def test_nonzero_level_with_yoy_clause_still_blocks_zero():
    """“决算1万元，比上年增加0.20万元”仍披露本期非零值，不能只看“增加”就跳过。"""
    doc = _doc(REAL_TEXT.replace(
        '公务接待费支出0万元。当年度根据实际工作情况，未安排公务接待。',
        '公务接待费支出决算1万元，比上年增加0.20万元。'))
    assert narrative_zero_sources(doc, table_code='FIN_07_three_public',
                                  indicator='reception', stage='决算') is None
