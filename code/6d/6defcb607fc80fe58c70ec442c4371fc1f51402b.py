"""把普陀区决算汇总表的部门/单位名单导入指定组织目录（干跑用副本目录，实跑用生产目录）。

归属规则来自《普陀区2025年度决算公开汇总表 v2.5》：单位代码前 3 位对应部门，
64 个代码组已逐一唯一解析；16 个无单位代码的行按用户裁定本次不导入。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROSTER = HERE / "derived_roster.json"

# 部门名以“部门决算”页为准，单位页写法不一致时的三组对照
DEPT_ALIAS = {
    "中共上海市普陀区委统一战线工作部": "中共上海市普陀区委统战部",
    "中国共产党上海市普陀区委老干部局": "中共上海市普陀区委老干部局",
    "上海市普陀区重大项目推进办公室": "上海市普陀区重大项目推进办公室（上海市普陀区区域协同发展办公室）",
}


def build_rows(district_name: str) -> tuple[list[dict], dict]:
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for dept in roster["departments"]:
        rows.append({"名称": dept["name"], "层级": "department", "上级": district_name, "代码": ""})

    prefix_to_dept = roster["prefix_to_dept"]
    skipped_codeless: list[str] = []
    units_without_parent: list[str] = []
    for unit in roster["units"]:
        if not unit["code"]:
            skipped_codeless.append(unit["name"])
            continue
        dept = prefix_to_dept.get(unit["code"][:3])
        dept = DEPT_ALIAS.get(dept or "", dept)
        if not dept:
            units_without_parent.append(unit["name"])
            continue
        rows.append({"名称": unit["name"], "层级": "unit", "上级": dept, "代码": unit["code"]})

    stats = {
        "departments": len(roster["departments"]),
        "unit_rows": len(roster["units"]),
        "imported_units": len(roster["units"]) - len(skipped_codeless),
        "skipped_codeless": skipped_codeless,
        "units_without_parent": units_without_parent,
    }
    return rows, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, help="organizations.json 所在目录")
    parser.add_argument("--district", default="普陀区")
    args = parser.parse_args()

    os.environ["ORG_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(REPO))
    from src.services.org_storage import get_org_storage

    rows, stats = build_rows(args.district)
    print(json.dumps({k: v for k, v in stats.items() if k != "skipped_codeless"}, ensure_ascii=False, indent=2))
    print(f"待导入行数: {len(rows)}")

    storage = get_org_storage()
    result = storage.import_from_list(rows)
    print(f"success={result.success} imported={result.imported} skipped={result.skipped} attempted={result.attempted}")
    if result.errors:
        print(f"ERRORS ({len(result.errors)}):")
        for err in result.errors:
            print("  -", err)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
