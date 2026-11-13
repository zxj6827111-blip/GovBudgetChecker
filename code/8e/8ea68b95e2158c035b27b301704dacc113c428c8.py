"""从普陀区决算公开汇总表推导组织目录导入数据集（只读演练，不写生产目录）。"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.services.org_name_identity import identity_name

SOURCE = Path("/Users/zhangxingjin/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
              "xwechat_files/wxid_lrcvinm6kqp22_a224/msg/file/2026-11/"
              "普陀区2025年度决算公开汇总表v2.5-20261029（发送稿）.xlsx")

# 单位决算页里的写法 -> 部门决算页里的规范写法（部门名以“部门决算”页为准）
DEPT_ALIAS = {
    "中共上海市普陀区委统一战线工作部": "中共上海市普陀区委统战部",
    "中国共产党上海市普陀区委老干部局": "中共上海市普陀区委老干部局",
    "上海市普陀区重大项目推进办公室": "上海市普陀区重大项目推进办公室（上海市普陀区区域协同发展办公室）",
}


def strip_paren_suffix(name: str) -> str:
    return re.sub(r"（[^（）]*）$", "", name).strip()


def load_rows() -> tuple[list[dict], list[dict]]:
    import openpyxl

    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    depts = [{"name": str(r[0]).strip()} for r in wb["部门决算"].iter_rows(min_row=2, values_only=True)
             if r and str(r[0] or "").strip()]
    units = [{"code": str(r[0] or "").strip(), "name": str(r[1]).strip()}
             for r in wb["单位决算"].iter_rows(min_row=2, values_only=True)
             if r and str(r[1] or "").strip()]
    return depts, units


def build_dept_index(dept_names: list[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for n in dept_names:
        index[n].add(n)
        index[strip_paren_suffix(n)].add(n)
        index[n.replace("中共", "中国共产党", 1)].add(n)
        index[f"{n}本级"].add(n)
        index[f"{strip_paren_suffix(n)}本级"].add(n)
    return index


def find_dept(cand: str, index: dict[str, set[str]]) -> tuple[str | None, str]:
    cand = cand.strip()
    for key in (DEPT_ALIAS.get(cand), cand, strip_paren_suffix(cand),
               f"{cand}本级", f"{strip_paren_suffix(cand)}本级"):
        if not key:
            continue
        hits = index.get(key)
        if hits and len(hits) == 1:
            return next(iter(hits)), f"alias:{key}" if key == DEPT_ALIAS.get(cand) else f"exact:{key}"
        if hits:
            return None, f"ambiguous:{key}->{sorted(hits)}"
    return None, "not-found"


def main() -> None:
    depts, units = load_rows()
    dept_names = [d["name"] for d in depts]
    index = build_dept_index(dept_names)

    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for u in units:
        if u["code"]:
            by_prefix[u["code"][:3]].append(u)

    prefix_to_dept: dict[str, str] = {}
    unresolved: list[dict] = []
    for prefix, group in sorted(by_prefix.items()):
        for u in group:
            dept, how = find_dept(u["name"], index)
            if dept:
                prefix_to_dept[prefix] = dept
                break
        else:
            unresolved.append({"prefix": prefix, "why": how,
                               "units": [u["name"] for u in group]})

    codeless = [u for u in units if not u["code"]]
    covered = set(prefix_to_dept.values())

    print(f"部门 {len(dept_names)} / 唯一 {len(set(dept_names))}；单位 {len(units)}；代码组 {len(by_prefix)}")
    print(f"未解析代码组 {len(unresolved)}")
    for item in unresolved:
        print("   ", item["prefix"], item["why"], item["units"][:3])
    print(f"无代码单位 {len(codeless)}: {[u['name'] for u in codeless]}")
    print(f"未被挂接的部门 {sorted(set(dept_names) - covered)}")

    catalog = json.loads((REPO / "data/organizations.json").read_text(encoding="utf-8"))
    existing = catalog["organizations"]
    by_identity: dict[tuple, list[str]] = defaultdict(list)
    for o in existing:
        by_identity[(o["level"], identity_name(o["name"]))].append(o["name"])
    new_nodes = [(n, "department") for n in dept_names] + [(u["name"], "unit") for u in units]
    collide = [(n, lv) for n, lv in new_nodes if by_identity.get((lv, identity_name(n)))]
    print(f"与现有目录同层同名节点 {len(collide)}")
    for n, lv in collide[:10]:
        print("   ", lv, n, "<->", by_identity[(lv, identity_name(n))])

    Path(__file__).with_name("derived_roster.json").write_text(json.dumps({
        "departments": depts,
        "units": units,
        "prefix_to_dept": prefix_to_dept,
        "codeless_units": codeless,
        "unresolved_prefixes": unresolved,
        "dept_without_unit": sorted(set(dept_names) - covered),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("derived_roster.json written")


if __name__ == "__main__":
    main()
