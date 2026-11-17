"""组织名的格式归一与身份比较；候选召回的包含匹配不能作为身份等价。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


def normalize_org_name(value: Any) -> str:
    # 横线的排版字形可不同；只统一等长的 em dash / horizontal bar，不删除字符或身份标记。
    name = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).replace("―", "—")
    # 双连字符/全角双连字符是中文双破折号的另一排版字形；保留两个字符，不删除名称组成。
    return re.sub(r"(?<!-)--(?!-)", "——", name)


def identity_name(value: Any) -> str:
    name = normalize_org_name(value)
    # 身份标记只统一写法，绝不删除；机关不等同于本级。
    return re.sub(
        r"\(?(本级|本部|机关|部门|单位|筹)\)?$",
        lambda m: "(" + ("本级" if m[1] in {"本级", "本部"} else m[1]) + ")",
        name,
    )


def _field(org: Any, key: str, default=None):
    return org.get(key, default) if isinstance(org, dict) else getattr(org, key, default)


def organization_name(org: Any) -> str:
    name, serialized = _field(org, "name"), _field(org, "organization_name")
    if name and serialized and identity_name(name) != identity_name(serialized):
        raise ValueError("organization name/organization_name contract conflict")
    return str(name or serialized or "")


def _admin_variants(name: str) -> set[str]:
    """生成规范行政前缀变体，仅用于等价主体归一，不移除任何身份后缀标记。"""
    variants = {name}
    if "人民政府" in name and "街道办事处" in name:
        variants.add(name.replace("人民政府", "", 1))
    if name.startswith("中国共产党") and "委员会" in name:
        variants.add(re.sub(r"^中国共产党(.+?)委员会", r"中共\1委", name))
        variants.add(re.sub(r"^中国共产党(.+?)委员会", r"中共\1委员会", name))
    elif name.startswith("中共") and "委员会" in name:
        variants.add(re.sub(r"^中共(.+?)委员会", r"中共\1委", name))
        variants.add(re.sub(r"^中共(.+?)委员会", r"中国共产党\1委员会", name))
    elif name.startswith("中共") and "委" in name:
        variants.add(re.sub(r"^中共(.+?)委", r"中共\1委员会", name, count=1))
        variants.add(re.sub(r"^中共(.+?)委", r"中国共产党\1委员会", name, count=1))
    if name.startswith("中国共产主义青年团"):
        variants.add("共青团" + name[len("中国共产主义青年团"):])
    elif name.startswith("共青团"):
        variants.add("中国共产主义青年团" + name[len("共青团"):])
    return {v for v in variants if v}


def _split_multi_agency_alias(name: str) -> tuple[str, list[str]] | None:
    marker = re.search(r"\((本级|机关|部门|单位|筹)\)$", name)
    if marker:
        return None
    alias = re.fullmatch(r"(.+)\(([^()]*)\)", name)
    if not alias:
        return None
    parts = [p.strip() for p in re.split(r"[、，,]", alias[2]) if p.strip()]
    if not parts:
        return None
    if all(
        len(p) >= 4
        and re.search(r"局|厅|中心|委员会|办公室|院|馆|所|站|处|队|学校|大学|政府", p)
        for p in parts
    ):
        return alias[1], parts
    return None


def _names(org: Any) -> set[str]:
    name = identity_name(organization_name(org))
    names = _admin_variants(name) if name else set()
    marker = re.search(r"\((本级|机关|部门|单位|筹)\)$", name)
    for keyword in _field(org, "keywords", []) or []:
        key = identity_name(keyword)
        key_marker = re.search(r"\((本级|机关|部门|单位|筹)\)$", key)
        # 目录关键词也不能把本级/筹等身份标记抹掉。
        if (marker.group() if marker else None) == (key_marker.group() if key_marker else None):
            names.update(_admin_variants(key))
    multi = _split_multi_agency_alias(name)
    if multi:
        # 仅末尾完整机构名（含多牌合署、或顿号分隔列表）是括号别名；（上海）、（林业）等正式组成不拆。
        primary, parts = multi
        names.update(_admin_variants(primary))
        for part in parts:
            names.update(_admin_variants(part))
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
        self._owner_identities: list[str] = []
        self._org_aliases_by_identity: dict[int, set[str]] = {}
        for index, org in enumerate(directory):
            self._owner_identities.append(identity_name(organization_name(org)))
            aliases = {_region_key(n) for n in _names(org)}
            self._org_aliases_by_identity[id(org)] = aliases
            for key in aliases:
                self.owners.setdefault(key, set()).add(index)

    def matches_org(self, text: Any, org: Any) -> NameComparison:
        """判断文本是否精确等价于候选组织（支持该组织独占别名，不跨越竞争主体）。"""
        comp = self.compare(text, organization_name(org))
        if comp.equivalent:
            return comp
        key = _region_key(identity_name(text))
        aliases = self._org_aliases_by_identity.get(id(org))
        if aliases is None:
            aliases = {_region_key(n) for n in _names(org)}
        if key and key in aliases:
            owners = self.owners.get(key, set())
            if self._same_canonical_owners(owners):
                return NameComparison(True, "exclusive_org_keyword")
        return comp

    def _same_canonical_owners(self, owners: set[int]) -> bool:
        if len(owners) <= 1:
            return len(owners) == 1
        return len({self._owner_identities[idx] for idx in owners}) == 1

    def compare(self, left: Any, right: Any) -> NameComparison:
        a, b = identity_name(left), identity_name(right)
        if not a or not b:
            return NameComparison(False, "empty")
        if a == b:
            return NameComparison(True, "format_or_identity_marker")
        if a in _admin_variants(b) or b in _admin_variants(a):
            if not self.owners or (
                _region_key(a) not in self.owners
                or _region_key(b) not in self.owners
                or self.owners.get(_region_key(a)) == self.owners.get(_region_key(b))
            ):
                return NameComparison(True, "admin_prefix_variant")
        for cand, other in ((a, b), (b, a)):
            multi = _split_multi_agency_alias(cand)
            if multi:
                primary, _ = multi
                if identity_name(primary) == other or other in _admin_variants(primary):
                    # 只有目录里没有互不相干的另一个同名全称竞争者时，才将合署多牌括号视为等价。
                    other_owners = self.owners.get(_region_key(other), set())
                    cand_owners = self.owners.get(_region_key(cand), set())
                    if not cand_owners or not other_owners or cand_owners == other_owners:
                        return NameComparison(True, "parenthetical_multi_agency_alias")
        owners_a = self.owners.get(_region_key(a), set())
        owners_b = self.owners.get(_region_key(b), set())
        if owners_a and owners_a == owners_b and self._same_canonical_owners(owners_a):
            return NameComparison(True, "unique_directory_alias_or_region")
        return NameComparison(False, "different_or_ambiguous")

    def has_name(self, value: Any) -> bool:
        """目录中是否存在与该值精确等价的条目（含已登记别名）。"""
        name = identity_name(value)
        return bool(name and self.owners.get(_region_key(name)))


def compare_org_names(left: Any, right: Any, directory: Iterable[Any] = ()) -> NameComparison:
    return OrgNameIndex(directory).compare(left, right)
