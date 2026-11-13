"""导入后校验：以 Excel 原件为独立对照，逐项核对目录、代码、归属与旧数据未被改动。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = Path("/Users/zhangxingjin/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
              "xwechat_files/wxid_lrcvinm6kqp22_a224/msg/file/2026-11/"
              "普陀区2025年度决算公开汇总表v2.5-20261029（发送稿）.xlsx")
ALIAS = {
    "中共上海市普陀区委统一战线工作部": "中共上海市普陀区委统战部",
    "中国共产党上海市普陀区委老干部局": "中共上海市普陀区委老干部局",
    "上海市普陀区重大项目推进办公室": "上海市普陀区重大项目推进办公室（上海市普陀区区域协同发展办公室）",
}


def excel_rows(snapshot: Path):
    data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    return data["departments"], [(u["code"], u["name"]) for u in data["units"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--baseline", required=True, help="导入前的 organizations.json 快照")
    parser.add_argument("--links-baseline", required=True, help="导入前的 job_org_links.json 快照")
    parser.add_argument("--snapshot", default=str(HERE / "source_snapshot.json"))
    args = parser.parse_args()

    root = Path(args.data_dir).resolve()
    payload = json.loads((root / "organizations.json").read_text(encoding="utf-8"))
    orgs = payload["organizations"]
    by_id = {o["id"]: o for o in orgs}
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))["organizations"]
    dept_names, units = excel_rows(Path(args.snapshot))

    fails: list[str] = []
    checks: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        (checks if ok else fails).append(f"{label}{(' — ' + detail) if detail else ''}")

    pt = next((o["id"] for o in orgs if o["name"] == "普陀区" and o["level"] == "district"), None)
    check("普陀区 district 节点存在", pt is not None)

    # 1. 65 个部门全部落位，且在普陀区下、名称逐字等于 Excel
    for name in dept_names:
        hits = [o for o in orgs if o["level"] == "department" and o["name"] == name]
        if len(hits) != 1:
            check(f"部门唯一存在：{name}", False, f"命中 {len(hits)} 个")
        elif hits[0]["parent_id"] != pt:
            check(f"部门上级为普陀区：{name}", False, f"实际 {by_id.get(hits[0]['parent_id'], {}).get('name')}")
    check(f"Excel 部门 {len(dept_names)} 个全部逐字命中且挂在普陀区下",
          not [f for f in fails if f.startswith("部门")])

    # 2. 单位：有代码的 312 个必须存在、代码逐字相等、父级=前缀对应部门
    expected_children: dict[str, list[tuple[str, str]]] = {}
    codeless = []
    for code, name in units:
        if not code:
            codeless.append(name)
            continue
        expected_children.setdefault(code[:3], []).append((code, name))

    prefix_dept = {}
    # 前缀->部门：组内单位名去掉“本级/括号尾/别名”后，只保留确实是 Excel 部门的那些
    for prefix, group in expected_children.items():
        cand = set()
        for _, name in group:
            for variant in (name, name[:-2] if name.endswith("本级") else name):
                variant = ALIAS.get(variant, re.sub(r"（[^（）]*）$", "", variant).strip())
                if variant in dept_names:
                    cand.add(variant)
        if len(cand) != 1:
            check(f"前缀 {prefix} 部门可唯一确定", False, str(sorted(cand)))
            continue
        prefix_dept[prefix] = next(iter(cand))
    check(f"64 个代码组全部唯一映射到部门（实到 {len(prefix_dept)}）", len(prefix_dept) == 64)
    check("不同代码组不会映射到同一部门（一部门一组）",
          len(set(prefix_dept.values())) == len(prefix_dept),
          f"重复 {[d for d in set(prefix_dept.values()) if list(prefix_dept.values()).count(d) > 1]}")

    ok_units = missing = bad_code = bad_parent = 0
    for prefix, group in expected_children.items():
        dept = prefix_dept.get(prefix)
        if not dept:
            continue
        dept_node = next((o for o in orgs if o["level"] == "department" and o["name"] == dept), None)
        for code, name in group:
            hits = [o for o in orgs if o["level"] == "unit" and o["name"] == name]
            if len(hits) != 1:
                missing += 1
                fails.append(f"单位缺失或重复：{name}（命中 {len(hits)}）")
                continue
            node = hits[0]
            if node.get("code") != code:
                bad_code += 1
                fails.append(f"单位代码不符：{name} 目录={node.get('code')} Excel={code}")
            if dept_node and node["parent_id"] != dept_node["id"]:
                bad_parent += 1
                fails.append(f"单位归属不符：{name} 实际父级={by_id.get(node['parent_id'], {}).get('name')} 应为={dept}")
            if node["parent_id"] != pt and node["parent_id"] in by_id and by_id[node["parent_id"]]["level"] != "department":
                fails.append(f"单位父级不是部门：{name}")
            ok_units += 1
    expected_total = sum(len(g) for g in expected_children.values())
    check(f"有代码单位全部纳入校验（{ok_units}/{expected_total}）", ok_units == expected_total,
          f"漏校验 {expected_total - ok_units}")
    check(f"{expected_total} 个有代码单位全部存在", missing == 0, f"缺失/重复 {missing}")
    check("单位 code 字段逐字等于 Excel 单位代码", bad_code == 0, f"不符 {bad_code}")
    check("每个单位的父级 = 其代码前缀对应部门", bad_parent == 0, f"不符 {bad_parent}")

    # 3. 16 个无代码单位确认未导入
    leaked = [n for n in codeless if any(o["name"] == n for o in orgs)]
    check(f"{len(codeless)} 个无代码单位按裁定全部未导入", not leaked, f"却出现 {leaked}")

    # 4. 反向：目录里普陀区 subtree 只多出 Excel 里的节点
    subtree_ids = {o["id"] for o in orgs if _in_pt(o, by_id, pt)}
    base_ids = {o["id"] for o in baseline}
    added = [o for o in orgs if o["id"] not in base_ids]
    check(f"新增节点数 = 377（实到 {len(added)}）", len(added) == 377)
    check("新增节点全部位于普陀区 subtree",
          all(o["id"] in subtree_ids for o in added))
    expect_new = {n for n in dept_names} | {n for _, n in units if n not in codeless}
    check("新增节点名称全部出自 Excel（无多余自造节点）",
          all(o["name"] in expect_new for o in added),
          f"多余 {sorted({o['name'] for o in added} - expect_new)[:5]}")

    # 4b. 反向：从目录出发核对每个新部门的子节点，代码前缀必须齐整且回指该部门
    dept_to_prefix = {v: k for k, v in prefix_dept.items()}
    expected_empty = set(dept_names) - set(prefix_dept.values())
    reverse_bad, reverse_units = [], 0
    for name in dept_names:
        node = next((o for o in orgs if o["level"] == "department" and o["name"] == name), None)
        kids = [o for o in orgs if o.get("parent_id") == node["id"]]
        reverse_units += len(kids)
        if not kids:
            if name not in expected_empty:
                reverse_bad.append(f"{name} 应有子单位却为 0")
            continue
        pre = {k.get("code", "")[:3] for k in kids}
        if len(pre) != 1 or next(iter(pre)) != dept_to_prefix.get(name):
            reverse_bad.append(f"{name} 子节点前缀 {sorted(pre)} 应为 {dept_to_prefix.get(name)}")
    check("每个新部门的子单位代码前缀齐整且回指本部门", not reverse_bad, str(reverse_bad[:3]))
    check(f"新部门下的子单位总数 = 312（实到 {reverse_units}）", reverse_units == expected_total)

    # 5. 旧数据未被改动
    old_changed = []
    for o in baseline:
        cur = by_id.get(o["id"])
        if cur is None:
            old_changed.append(f"节点消失 {o['name']}")
        elif (cur["name"], cur["level"], cur.get("parent_id"), cur.get("code")) != (o["name"], o["level"], o.get("parent_id"), o.get("code")):
            old_changed.append(f"字段变动 {o['name']}")
    check(f"导入前 {len(baseline)} 个节点逐一未被改动", not old_changed, f"{old_changed[:3]}")

    links_md5 = hashlib.md5((root / "job_org_links.json").read_bytes()).hexdigest()
    base_links = hashlib.md5(Path(args.links_baseline).read_bytes()).hexdigest()
    check("job_org_links.json 未被改写", links_md5 == base_links, f"{links_md5[:8]} vs {base_links[:8]}")

    # 6. 层级校验器复核
    sys.path.insert(0, str(REPO))
    from src.services.org_hierarchy import validate_organization_hierarchy
    res = validate_organization_hierarchy(orgs)
    check("org_hierarchy 校验通过", res.valid, "; ".join(res.errors[:5]))

    print("== PASS ==")
    for c in checks:
        print("  [OK]", c)
    if fails:
        print("== FAIL ==")
        for f in fails[:40]:
            print("  [X]", f)
    from collections import Counter
    print("== 规模 ==")
    print("  层级分布:", dict(Counter(o["level"] for o in orgs)))
    print("  普陀区下部门:", len([o for o in orgs if o["level"] == "department" and o["parent_id"] == pt]))
    print(f"  失败 {len(fails)} 项 / 通过 {len(checks)} 项")
    return 1 if fails else 0


def _in_pt(org, by_id, pt) -> bool:
    seen, cur = set(), org
    while cur and cur["id"] not in seen:
        seen.add(cur["id"])
        if cur["id"] == pt:
            return True
        cur = by_id.get(cur.get("parent_id"))
    return False


if __name__ == "__main__":
    raise SystemExit(main())
