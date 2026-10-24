"""组织名的格式归一与身份比较；候选召回的包含匹配不能作为身份等价。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


def normalize_org_name(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def identity_name(value: Any) -> str:
    name = normalize_org_name(value)
    # 身份标记只统一写法，绝不删除；机关不等同于本级。
    return re.sub(r"\(?(本级|本部|机关|部门|单位|筹)\)?$",
                  lambda m: "(" + ("本级" if m[1] in {"本级", "本部"} else m[1]) + ")", name)


def _field(org: Any, key: str, default=None):
    return org.get(key, default) if isinstance(org, dict) else getattr(org, key, default)


def organization_name(org: Any) -> str:
    name, serialized = _field(org, "name"), _field(org, "organization_name")
    if name and serialized and identity_name(name) != identity_name(serialized):
        raise ValueError("organization name/organization_name contract conflict")
    return str(name or serialized or "")


def _names(org: Any) -> set[str]:
    name = identity_name(organization_name(org))
    names = {name} if name else set()
    marker = re.search(r"\((本级|机关|部门|单位|筹)\)$", name)
    for keyword in _field(org, "keywords", []) or []:
        key = identity_name(keyword)
        key_marker = re.search(r"\((本级|机关|部门|单位|筹)\)$", key)
        # 目录关键词也不能把本级/筹等身份标记抹掉。
        if (marker.group() if marker else None) == (key_marker.group() if key_marker else None):
            names.add(key)
    alias = re.fullmatch(r"(.+)\(([^()]*)\)", name)
    if alias and not marker and len(alias[2]) >= 4 and re.search(
        r"局|厅|中心|委员会|办公室|院|馆|所|站|处|队|学校|大学|政府", alias[2]
    ):
        # 仅末尾完整机构名是括号别名；（上海）、（林业）等正式组成不拆。
        names.update(alias.groups())
    return {item for item in names if item}


def _region_key(name: str) -> str:
    return re.sub(r"^上海市?", "上海市", name)


@dataclass(frozen=True)
class NameComparison:
    equivalent: bool
    rule: str


class OrgNameIndex:
    """同一目录快照只构建一次索引，避免全量预检出现平方级目录扫描。"""
    def __init__(self, directory: Iterable[Any] = ()):
        self.owners: dict[str, set[int]] = {}
        for index, org in enumerate(directory):
            for name in _names(org):
                self.owners.setdefault(_region_key(name), set()).add(index)

    def compare(self, left: Any, right: Any) -> NameComparison:
        a, b = identity_name(left), identity_name(right)
        if not a or not b:
            return NameComparison(False, "empty")
        if a == b:
            return NameComparison(True, "format_or_identity_marker")
        owners_a = self.owners.get(_region_key(a), set())
        owners_b = self.owners.get(_region_key(b), set())
        if len(owners_a) == len(owners_b) == 1 and owners_a == owners_b:
            return NameComparison(True, "unique_directory_alias_or_region")
        return NameComparison(False, "different_or_ambiguous")


def compare_org_names(left: Any, right: Any, directory: Iterable[Any] = ()) -> NameComparison:
    return OrgNameIndex(directory).compare(left, right)
