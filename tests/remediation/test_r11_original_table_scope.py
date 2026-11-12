"""R11-01 原件侧表范围固定预期回归（2026-11-12）。

表外章节标题/年度/页脚不得进入原件侧金额与行身份；续页网格边框缺失
时首末数据行与交错名称续行仍须保留；表内年份形态金额负控不回退。
预期来自 Codex 第十一轮核验包冻结断言（老干部活动室第5页）。
"""
from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from src.services.pdf_parser import PDFParser  # noqa: E402
import d1_table_reconciliation as d1  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "r11" / "section_title_year.pdf"


@pytest.mark.parametrize('text,expected', [
    ('一、一般公共预算财政拨款收入 100.00 80.00', True),
    ('2080506 141.55 141.55', True),
    ('2025 年', False),
    ('第二部分 测试单位 2025 年', False),
    ('度决算表', False),
    ('单位：万元', False),
    ('注：本表反映各项收入情况。', False),
    ('12', False),
])
def test_scope_data_line_criterion(text, expected):
    assert d1._scope_data_line(text) is expected


@pytest.mark.parametrize('text,expected', [
    ('费支出', True),
    ('机关事业单位职业年金缴', True),
    ('出', True),
    ('度决算表', False),
    ('第二部分', False),
    ('注：本表反映', False),
    ('上海市粮食和物资储备局老干部活动室', False),
    ('2025', False),
])
def test_scope_name_fragment_criterion(text, expected):
    assert d1._scope_name_fragment(text) is expected


def test_section_title_year_is_not_an_amount():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    report = d1.reconcile_auto(str(FIXTURE))
    assert report['gap'] == 0
    assert report['pass'] >= 1
    target = next(r for r in report['results']
                  if r['table_code'] == 'FIN_01_income_expenditure_total')
    assert target['verdict'] == 'PASS'
    assert '2025' not in (target['missing_numbers'] or {})
    assert '2025' not in (target['extra_numbers'] or {})
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == digest


def test_in_table_amount_mutation_still_gap():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    artifact = PDFParser(None).build_parse_artifact(str(FIXTURE))
    bad = copy.deepcopy(artifact)
    changed = False
    for fragment in list(bad.table_fragments) + list(bad.physical_fragments):
        for ri, row in enumerate(fragment.rows):
            if len(row) >= 2 and str(row[1]).strip() == '100.00':
                row[1] = '101.00'
                for cell in fragment.cells:
                    if cell.row_idx == ri and cell.col_idx == 1:
                        cell.raw_text = '101.00'
                changed = True
                break
        if changed:
            break
    assert changed
    with patch.object(d1.PDFParser, 'build_parse_artifact', return_value=bad):
        report = d1.reconcile_auto(str(FIXTURE))
    assert report['gap'] >= 1
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == digest


def test_scoped_text_excludes_title_keeps_table_rows():
    import pdfplumber
    with pdfplumber.open(str(FIXTURE)) as doc:
        scoped = d1._table_scoped_texts(doc)
    text = scoped[0]
    assert '2025' not in text
    assert '度决算表' not in text
    assert '本年收入合计' in text
    assert '100.00' in text