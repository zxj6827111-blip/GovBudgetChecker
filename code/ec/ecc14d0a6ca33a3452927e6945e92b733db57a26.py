from reproduce_edges import title, trend


def test_decline_outside_frozen_rounding_range_must_still_report():
    issues = trend('公务接待费支出决算1.17万元。\n'
                   '公务接待费支出决算减少0.31万元，下降21.40%。')
    assert any('复算不一致' in x['message'] for x in issues), issues


def test_one_decimal_must_not_expand_frozen_005_rounding_policy():
    issues = trend('公务接待费支出决算0.70万元。\n'
                   '公务接待费支出决算增加0.4万元，增长150%。')
    assert any('复算不一致' in x['message'] for x in issues), issues


def test_template_normalization_must_preserve_organization_name():
    issues = title('上海市事业登记管理局')
    assert any('目录与正文标题不一致' in x['message'] for x in issues), issues


def test_identical_organization_name_remains_clean():
    assert title('上海市事业单位登记管理局') == []
