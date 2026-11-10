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


def subject_variant_key(value: str) -> str:
    # 字形变体仅用于候选定位，正式名称仍按原文逐字比较。
    return subject_suffix_key(value).replace("其它", "其他")


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
