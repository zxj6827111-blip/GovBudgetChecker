"""功能科目名称的分层定位键；定位成功不代表原文名称一致。

只提供文本候选，不读取金额，也不证明科目身份。金额规则还须校验原表
编码、父级、完整候选集和原生单元格证据，不能直接把此处的候选当作事实。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping


def literal_subject_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).strip('“”"「」『』')


def subject_suffix_key(value: str) -> str:
    """只剥一层末尾修饰，保留至少四字的科目主体，避免“其他支出”等短名碰撞。"""
    name = literal_subject_name(value)
    for suffix in ("预算支出", "决算支出", "支出决算", "支出", "预算", "决算", "费用", "费"):
        if name.endswith(suffix) and len(name) - len(suffix) >= 4:
            return name[:-len(suffix)]
    return name


_LEGACY_SUBJECT_ALIASES = {
    "行政事业单位离退休": "行政事业单位养老",
    "其他行政事业单位离退休": "其他行政事业单位养老",
    "工业和信息产业监管": "工业和信息产业",
    "文化体育与传媒": "文化旅游体育与传媒",
    "其他文化体育与传媒": "其他文化旅游体育与传媒",
    "中专教育": "中等职业教育",
    "医疗卫生与计划生育": "卫生健康",
    "医疗卫生健康": "卫生健康",
    "医疗卫生": "卫生健康",
    "其他医疗卫生与计划生育": "其他卫生健康",
    "其他医疗卫生健康": "其他卫生健康",
    "国土海洋气象等": "自然资源海洋气象等",
    "其他国土海洋气象等": "其他自然资源海洋气象等",
}


def subject_variant_key(value: str) -> str:
    # 字形变体与常见历史科目沿用名仅用于候选定位，正式名称仍按原文逐字比较。
    key = subject_suffix_key(value).replace("其它", "其他")
    for prefix in ("归口管理的",):
        if key.startswith(prefix) and len(key) - len(prefix) >= 4:
            key = key[len(prefix):]
            break
    return _LEGACY_SUBJECT_ALIASES.get(key, key)


def subject_name_candidates(name: str, names: Mapping[str, str]) -> list[str]:
    """逐字相同优先于有限变体；同档多候选全部返回，不能用较弱匹配消歧。"""
    literal = literal_subject_name(name)
    if not literal:
        return []
    exact = sorted(code for code, value in names.items() if literal_subject_name(value) == literal)
    if exact:
        return exact
    key = subject_variant_key(name)
    return sorted(code for code, value in names.items() if subject_variant_key(value) == key)


def scoped_subject_name_variant(code: str, printed: str) -> str | None:
    """仅允许在印刷编码确认后识别该科目的特定文字差异，避免短名称跨款合并。"""
    if code == "2130110" and literal_subject_name(printed) == "执法监管":
        return "执法监督"
    return None
