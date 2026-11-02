"""组织目录只读演练与副本清洗。仅严格重复可合并；近似项交人工确认。"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.org_name_identity import identity_name, normalize_org_name, _names
from src.services.org_storage import organization_name_warning


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def _keeper(values, referenced_ids):
    """保留项优先取仍有材料引用的记录，其次最早创建，最后按 id 定序。"""
    return min(
        values,
        key=lambda org: (
            0 if org["id"] in referenced_ids else 1,
            org.get("created_at", 0),
            org["id"],
        ),
    )


def _resolve(mapping, org_id):
    while org_id in mapping:
        org_id = mapping[org_id]
    return org_id


def plan_directory(payload, review_groups=(), approve_groups=(), referenced_ids=()):
    groups, mapping, review = {}, {}, []
    rows = payload.get("organizations", [])
    referenced = frozenset(str(item) for item in (referenced_ids or ()))
    for org in rows:
        groups.setdefault(
            (org["level"], org.get("parent_id"), normalize_org_name(org["name"])), []
        ).append(org)
        if organization_name_warning(org["name"]):
            review.append(
                {
                    "kind": "tail_warning",
                    "id": org["id"],
                    "name": org["name"],
                    "reason": "需确认名称尾部年度/文种",
                }
            )
        if org["level"] == "unit" and identity_name(org["name"]).endswith("(部门)"):
            review.append(
                {
                    "kind": "level_review",
                    "id": org["id"],
                    "name": org["name"],
                    "reason": "层级与名称部门标记冲突，禁止自动改层级",
                }
            )
    for values in groups.values():
        keep = _keeper(values, referenced)
        mapping.update({org["id"]: keep["id"] for org in values if org["id"] != keep["id"]})
    near = {}
    for org in rows:
        for variant in _names(org):
            near.setdefault((org["level"], org.get("parent_id"), variant), []).append(org)
    for values in near.values():
        if len(values) > 1 and len({normalize_org_name(org["name"]) for org in values}) > 1:
            review.extend(
                {
                    "kind": "identity_variant_review",
                    "id": org["id"],
                    "name": org["name"],
                    "reason": "本级/本部近似项，仅列出供确认",
                }
                for org in values
            )
    # 验收方的待确认组可以包含“机关/本级”“筹”等实质差别，不能交给等价比较器自动合并。
    # 此清单只追加人工核查行，完全不参与 mapping 和 cleaned 的计算。
    for group_index, names in enumerate(review_groups, 1):
        keys = {normalize_org_name(name) for name in names}
        for org in rows:
            if normalize_org_name(org["name"]) in keys:
                review.append({"kind": "external_group_review", "id": org["id"],
                               "name": org["name"],
                               "reason": f"独立清单第{group_index}组：{' / '.join(names)}；仅供核对，不认定等价"})
    # 经用户明确批准的写法变体组（如“局本级 / 局本部 / 局（本级）”）才允许合并。
    # 等价比较器只用来校验同组确实等价，不能用它自行授权合并“机关/本级”这类实质差别。
    by_id = {org["id"]: org for org in rows}
    by_name = {}
    for org in rows:
        by_name.setdefault(normalize_org_name(org["name"]), []).append(org)
    for group_index, names in enumerate(approve_groups, 1):
        label = f"批准的变体组第{group_index}组（{' / '.join(names)}）"
        if len({identity_name(name) for name in names}) != 1:
            raise ValueError(f"{label}身份不一致，拒绝合并")
        members = {
            org["id"]: org
            for name in names
            for org in by_name.get(normalize_org_name(name), [])
        }
        if len(members) < 2:
            review.append(
                {
                    "kind": "approve_group_unmatched",
                    "id": "",
                    "name": " / ".join(names),
                    "reason": "批准组未命中至少两条目录记录，未执行合并",
                }
            )
            continue
        if len({(org["level"], org.get("parent_id")) for org in members.values()}) != 1:
            raise ValueError(f"{label}层级或上级不一致，拒绝合并")
        survivors = {_resolve(mapping, org_id) for org_id in members}
        keep = _keeper([by_id[org_id] for org_id in survivors], referenced)
        for org_id in survivors:
            if org_id != keep["id"]:
                mapping[org_id] = keep["id"]
    # 已并入保留项的记录不再存在，继续列为“待确认”会让复核人对着已删除的条目核对。
    review = [item for item in review if item["id"] not in mapping]
    retained = []
    for org in rows:
        if org["id"] in mapping:
            continue
        retained.append(
            {**org, "parent_id": mapping.get(org.get("parent_id"), org.get("parent_id"))}
        )
    return {**payload, "organizations": retained}, mapping, review


def rewrite_ids(value, mapping):
    if isinstance(value, dict):
        return {
            key: (
                mapping.get(item, item)
                if key in {"org_id", "organization_id", "department_id", "parent_id"}
                and isinstance(item, str)
                else rewrite_ids(item, mapping)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [rewrite_ids(item, mapping) for item in value]
    return value


#: 组织引用列不只叫 org_id：material_slots 用 subject_org_id/department_org_id/
#: jurisdiction_org_id，reporting_tasks 用 jurisdiction_org_id。漏扫会错误宣称已核对。
ORG_REFERENCE_COLUMN_SQL = (
    "SELECT table_schema, table_name, column_name FROM information_schema.columns "
    "WHERE table_schema='public' AND (column_name LIKE '%org_id%' "
    "OR column_name IN ('organization_id','department_id','unit_id')) "
    "ORDER BY table_name, column_name"
)


async def database_references(dsn, mapping):
    if not dsn:
        return {
            "status": "unverified",
            "reason": "未显式提供只读数据库连接；不能宣称引用已完整核对",
            "tables": [],
        }
    import asyncpg

    conn = await asyncpg.connect(dsn)
    tables = []
    try:
        async with conn.transaction(readonly=True):
            columns = await conn.fetch(ORG_REFERENCE_COLUMN_SQL)
            for row in columns:
                schema, table, column = (
                    str(row[key]).replace('"', '""')
                    for key in ("table_schema", "table_name", "column_name")
                )
                found = await conn.fetch(
                    f'SELECT "{column}"::text AS org_id, count(*) AS references FROM "{schema}"."{table}" WHERE "{column}"::text = ANY($1::text[]) GROUP BY "{column}"::text',
                    list(mapping),
                )
                tables.append(
                    {
                        "table": f"{schema}.{table}",
                        "column": column,
                        "references": [dict(item) for item in found],
                    }
                )
    finally:
        await conn.close()
    return {"status": "checked_read_only", "tables": tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-data-dir", type=Path, required=True)
    parser.add_argument("--uploads-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--database-url-env", help="仅此显式环境变量供只读引用检查，不加载 .env")
    parser.add_argument(
        "--expected-orgs", type=Path, help="独立清单（JSON 名称数组），只报告缺失，不创建组织"
    )
    parser.add_argument("--review-groups", type=Path, help="独立冻结的近似名称组（JSON 二维数组），只输出人工核查，不参与合并")
    parser.add_argument(
        "--approve-groups",
        type=Path,
        help="已明确批准合并的写法变体组（JSON 二维数组，如 局本级/局（本级）），仅在身份、层级、上级一致时并入保留项",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write-copy", type=Path)
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = args.org_data_dir.resolve()
    if not (source / "organizations.json").is_file():
        parser.error("组织库文件不存在")
    if args.out.resolve().is_relative_to(source):
        parser.error("演练输出不得位于原组织数据目录内")
    payload = read(source / "organizations.json")
    links = read(source / "job_org_links.json")
    referenced_ids = {
        str(link["org_id"]) for link in links.get("links", []) if link.get("org_id")
    }
    cleaned, mapping, review = plan_directory(
        payload,
        review_groups=read(args.review_groups) if args.review_groups else (),
        approve_groups=read(args.approve_groups) if args.approve_groups else (),
        referenced_ids=referenced_ids,
    )
    if args.expected_orgs:
        expected = read(args.expected_orgs)
        existing_names = {
            normalize_org_name(org["name"]) for org in payload.get("organizations", [])
        }
        for name in expected:
            if normalize_org_name(name) not in existing_names:
                review.append(
                    {
                        "kind": "missing_organization",
                        "id": "",
                        "name": name,
                        "reason": "独立清单要求的组织未在目录中找到，需用户确认补录",
                    }
                )
    files = [source / "job_org_links.json"]
    if args.uploads_dir:
        files.extend(sorted(args.uploads_dir.rglob("status.json")))
    references, changed = [], {}
    for path in files:
        original = read(path)
        replacement = rewrite_ids(original, mapping)
        if replacement != original:
            references.append(
                {"path": str(path), "old_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
            changed[path] = replacement
    database = asyncio.run(
        database_references(
            os.environ.get(args.database_url_env) if args.database_url_env else None, mapping
        )
    )
    args.out.mkdir(parents=True, exist_ok=True)
    report = {
        "source": str(source),
        "source_sha256": hashlib.sha256((source / "organizations.json").read_bytes()).hexdigest(),
        "merge_ids": mapping,
        "references": references,
        "database": database,
        "original_count": len(payload.get("organizations", [])),
        "cleaned_count": len(cleaned["organizations"]),
        "review_groups_source": ({"path": str(args.review_groups.resolve()),
                                  "sha256": hashlib.sha256(args.review_groups.read_bytes()).hexdigest()}
                                 if args.review_groups else None),
        "approve_groups_source": ({"path": str(args.approve_groups.resolve()),
                                   "sha256": hashlib.sha256(args.approve_groups.read_bytes()).hexdigest()}
                                  if args.approve_groups else None),
        "limits": [
            "仅合并同层级同上级格式归一后完全相同的节点",
            "写法变体组须由 --approve-groups 明确列出，且身份、层级、上级一致才合并",
            "缺失组织必须由用户确认后补录",
            "近似别名不自动合并",
        ],
    }
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    with (args.out / "manual_review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "id", "name", "reason"])
        writer.writeheader()
        writer.writerows(review)
    if args.write_copy:
        target = args.write_copy.resolve()
        if (
            target == source
            or target.is_relative_to(source)
            or source.is_relative_to(target)
            or target.exists()
        ):
            parser.error("清洗副本须为与原目录互不包含的全新目录")
        target.mkdir(parents=True)
        for name in ("organizations.json", "job_org_links.json"):
            if (source / name).exists():
                shutil.copy2(source / name, target / name)
        (target / "organizations.json").write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2)
        )
        if (source / "job_org_links.json") in changed:
            (target / "job_org_links.json").write_text(
                json.dumps(changed[source / "job_org_links.json"], ensure_ascii=False, indent=2)
            )
    if args.apply:
        # DB 与文件不能假装原子迁移；有库引用时由后续获批迁移专门处理。
        if database["status"] != "checked_read_only" or any(
            row["references"] for row in database["tables"]
        ):
            parser.error(
                "正式应用被阻止：数据库引用尚未核实或仍需独立迁移。请先审阅演练报告并取得批准"
            )
        backup = args.out / "backup"
        backup.mkdir(exist_ok=False)
        changed[source / "organizations.json"] = cleaned
        try:
            for index, (path, value) in enumerate(changed.items()):
                shutil.copy2(path, backup / str(index))
                temp = path.with_name(path.name + ".cleanup.tmp")
                temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
                temp.replace(path)
        except Exception:
            for index, path in enumerate(changed):
                if (backup / str(index)).exists():
                    shutil.copy2(backup / str(index), path)
            raise
        (backup / "paths.json").write_text(
            json.dumps([str(p) for p in changed], ensure_ascii=False)
        )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
