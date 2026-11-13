"""完整空白三公表的等效表下说明；不靠缺表/缺数豁免。"""
from types import SimpleNamespace

import pytest

from src.engine.native_no_activity import require_no_activity_disposition
from src.engine.rule_outcome import RuleNotApplicable
from tests.remediation.test_putuo_code_repair import empty_three_public


@pytest.mark.parametrize('phrase', ['没有三公经费', '没有“三公”经费', '无财政拨款“三公”经费支出'])
def test_same_page_equivalent_empty_note_has_twelve_certified_sources(phrase):
    doc=empty_three_public()
    doc.page_texts=['甲局2024年度部门决算。说明：甲局本年度'+phrase+'，故本表无数据。']
    before=doc.fact_context.to_dict()
    with pytest.raises(RuleNotApplicable):
        require_no_activity_disposition(SimpleNamespace(code='test'),doc)
    assert doc.fact_context.to_dict()==before
    assert len(doc.fact_receipts[0]['cell_observations'])==12
    assert '故本表无数据' in doc.fact_receipts[0]['disclosure_sources'][0]['span']


@pytest.mark.parametrize('note', [
    '甲局本年度没有三公经费。',
    '甲局本年度预计没有三公经费，故本表无数据。',
    '如果甲局本年度没有三公经费，故本表无数据。',
    '“甲局本年度没有三公经费，故本表无数据。”',
    '“甲局本年度没有“三公”经费，故本表无数据。”',
    '以下为模板示例。甲局本年度没有三公经费，故本表无数据。',
    '甲局2023年度本年没有三公经费，故本表无数据。',
    '甲局本年度没有三公经费，故本表无数据？',
])
def test_unconfirmed_or_non_table_note_cannot_close_empty_amount_checks(note):
    doc=empty_three_public()
    doc.page_texts=['甲局2024年度部门决算。'+note]
    assert require_no_activity_disposition(SimpleNamespace(code='test'),doc) is None


def test_only_narrative_page_note_cannot_replace_table_below_note():
    doc=empty_three_public()
    doc.page_texts=['甲局2024年度部门决算。','甲局本年度没有三公经费，故本表无数据。']
    assert require_no_activity_disposition(SimpleNamespace(code='test'),doc) is None
