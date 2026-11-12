"""规则声明 YAML 顶层/嵌套键唯一性守卫（线2采纳项）。

来源：codex/automation-restoration-20261110 的
test_declaration_yaml_preserves_unique_budget_configuration。
主线 v3_3.yaml 曾存在重复顶层键 ``budget``（行 10 的真实配置块与行 963 的
旧版误置决算表别名块），PyYAML last-wins 会静默丢弃前一块。已移除死块；
本守卫防止再次引入重复键（同键静默覆盖是配置完整性隐患）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

RULES_FILE = Path(__file__).resolve().parents[1] / 'rules' / 'v3_3.yaml'


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _assert_unique_keys(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    assert len(keys) == len(set(keys)), keys
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _assert_unique_keys)


def test_rules_yaml_has_no_duplicate_mapping_keys():
    data = yaml.load(RULES_FILE.read_text(encoding='utf-8'), Loader=_UniqueKeyLoader)
    assert isinstance(data, dict)


def test_budget_section_is_the_single_real_contract():
    data = yaml.safe_load(RULES_FILE.read_text(encoding='utf-8'))
    # 移除误置别名块后，唯一 budget 块即顶部真实契约。
    assert data['budget']['scope']['applies_to'] == ['预算公开']
    assert 'nine_tables' not in data['budget']
