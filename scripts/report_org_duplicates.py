"""组织目录只读体检：列出同主体的多种写法，产出待人工确认的变体组候选。

本脚本不修改任何组织数据。输出分两类：
- 严格重复：格式归一（全/半角括号、空白）后完全相同，clean_org_directory 默认可合并；
- 写法变体：如「局本级 / 局本部 / 局（本级）」，必须逐组由人确认是同一主体后，
  才能作为 clean_org_directory 的 --approve-groups 输入。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.org_name_identity import identity_name, normalize_org_name


def classify(organizations):
    """按层级+上级+身份归一分组，返回 (严格重复组, 写法变体组)，组内按创建时间排序。"""
    groups = defaultdict(list)
    for org in organizations:
        groups[(org["level"], org.get("parent_id"), identity_name(org["name"]))].append(org)
    strict, variant = [], []
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda org: (org.get("created_at") or 0, org["id"]))
        bucket = strict if len({normalize_org_name(o["name"]) for o in members}) == 1 else variant
        bucket.append(ordered)
    return strict, variant


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, help="写出写法变体组候选 JSON（每行一组名称）")
    args = parser.parse_args()

    source = args.org_data_dir.resolve()
    path = source / "organizations.json"
    if not path.is_file():
        parser.error(f"组织库文件不存在：{path}")
    if args.out and args.out.resolve().is_relative_to(source):
        parser.error("输出文件不得写入组织数据目录")
    organizations = json.loads(path.read_text(encoding="utf-8-sig")).get("organizations", [])

    strict, variant = classify(organizations)
    print(
        f"组织总数 {len(organizations)}；"
        f"严格重复 {len(strict)} 组 / 冗余 {sum(len(g) - 1 for g in strict)} 条；"
        f"写法变体 {len(variant)} 组 / 冗余 {sum(len(g) - 1 for g in variant)} 条"
    )
    for group in strict:
        print("  严格重复:", " / ".join(org["name"] for org in group))
    for group in variant:
        print("  写法变体:", " / ".join(org["name"] for org in group), "  ← 需人工确认是否同一主体")

    if args.out:
        args.out.write_text(
            json.dumps([[org["name"] for org in group] for group in variant],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"候选清单已写入 {args.out}；逐组确认后再作为 --approve-groups 使用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
