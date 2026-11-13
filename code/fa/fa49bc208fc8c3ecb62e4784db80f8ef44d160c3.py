"""复核零证据的资金口径与矛盾排除，不修改业务代码。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path('/Volumes/workSSD/software development/gbc-worktrees/real-painpoint-fixes-20261104')
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('zero_fixture_review', ROOT / 'tests/remediation/test_narrative_zero_evidence.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from src.engine import native_empty_operands
assert Path(native_empty_operands.__file__).resolve() == ROOT / 'src/engine/native_empty_operands.py'

PREFIX = '甲局2024年度部门决算。七、财政拨款“三公”经费支出决算情况说明。'


def term(text, stage='决算'):
    return native_empty_operands.blank_term(
        fixture._doc(PREFIX + text), 'FIN_07_three_public', 'reception',
        code='total', stage=stage)


def test_non_fiscal_zero_cannot_prove_fiscal_blank():
    assert term('非财政拨款公务接待费支出决算0万元。') is None


def test_nonzero_level_with_yoy_clause_still_blocks_zero():
    assert term('公务接待费支出决算0万元。公务接待费支出决算1万元，比上年增加0.20万元。') is None


def test_current_fiscal_zero_positive():
    value = term('财政拨款公务接待费支出决算0万元。')
    assert value is not None
    assert str(value.normalized_value) == '0'
    assert value.raw_value == ''
    assert value.extra_dims['native_blank_observation']['narrative_sources']


def test_nonzero_without_yoy_control():
    assert term('公务接待费支出决算0万元。公务接待费支出决算1万元。') is None


def test_final_zero_does_not_fill_budget():
    assert term('财政拨款公务接待费支出决算0万元。', stage='年初预算') is None
