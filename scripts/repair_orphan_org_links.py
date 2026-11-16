"""幂等修复历史任务（uploads/*/status.json）与材料槽位（PG material_slots）中的孤儿组织 ID。

问题背景（2026-11-15 根因诊断）：
组织目录 `data/organizations.json` 曾重建过 ID，导致约 199 份历史 `uploads/<job_id>/status.json`
及 PG `material_slots` 中的 `organization_id` / `subject_org_id` 失效或呈 `unresolved:...`。
本脚本按 `OrgNameIndex`（与 `doc_type` / `subject_level` / 显式别名消歧）将其重新绑定到当前有效组织，
冲突槽位保持不动并如实报告，绝不静默覆盖或删除业务数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.org_name_identity import OrgNameIndex
from src.services.org_storage import OrganizationStorage

_EXPLICIT_NAME_ALIASES = {
    "中国自由贸易试验区临港新片区管理委员会": "中国（上海）自由贸易试验区临港新片区管理委员会",
}


def resolve_target_org(
    org_name: str,
    orgs: List[Any],
    idx: OrgNameIndex,
    *,
    preferred_level: Optional[str] = None,
) -> Tuple[Optional[Any], str]:
    """根据单位名称（及可选层级提示）在当前组织目录中唯一定位有效组织。"""
    clean_name = str(org_name or "").strip()
    if not clean_name or clean_name == "unresolved:anonymous":
        return None, "empty_or_anonymous"

    candidates = [o for o in orgs if idx.matches_org(clean_name, o).equivalent]
    if not candidates and clean_name in _EXPLICIT_NAME_ALIASES:
        aliased = _EXPLICIT_NAME_ALIASES[clean_name]
        candidates = [o for o in orgs if idx.matches_org(aliased, o).equivalent]

    if len(candidates) == 1:
        return candidates[0], "unique_name_match"

    if len(candidates) > 1 and preferred_level in ("department", "unit"):
        scoped = [o for o in candidates if getattr(o, "level", None) == preferred_level]
        if len(scoped) == 1:
            return scoped[0], f"level_disambiguated:{preferred_level}"

    if not candidates:
        return None, "no_match"
    return None, f"ambiguous:{len(candidates)}"


def _infer_preferred_level(status_data: Dict[str, Any]) -> Optional[str]:
    doc_type = str(status_data.get("doc_type") or "").strip().lower()
    if doc_type.startswith("dept"):
        return "department"
    if doc_type.startswith("unit"):
        return "unit"
    return None


def repair_upload_status_files(
    uploads_dir: Path,
    orgs: List[Any],
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """修复 uploads/*/status.json 中失效的 organization_id。"""
    by_id = {o.id: o for o in orgs}
    idx = OrgNameIndex(orgs)

    total = 0
    already_valid = 0
    repaired = 0
    skipped: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []

    for sf in sorted(uploads_dir.glob("*/status.json")):
        total += 1
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append({"job_id": sf.parent.name, "reason": f"json_error:{exc}"})
            continue

        old_id = str(data.get("organization_id") or "").strip()
        if old_id and old_id in by_id:
            already_valid += 1
            continue

        org_name = str(data.get("organization_name") or "").strip()
        if not org_name:
            skipped.append({"job_id": sf.parent.name, "reason": "no_organization_name", "filename": data.get("filename")})
            continue

        preferred = _infer_preferred_level(data)
        target, reason = resolve_target_org(org_name, orgs, idx, preferred_level=preferred)
        if target is None:
            skipped.append({
                "job_id": sf.parent.name,
                "reason": reason,
                "old_org_id": old_id,
                "org_name": org_name,
            })
            continue

        changes.append({
            "job_id": sf.parent.name,
            "old_org_id": old_id,
            "new_org_id": target.id,
            "old_name": org_name,
            "new_name": target.name,
            "level": target.level,
            "reason": reason,
        })
        if apply:
            data["organization_id"] = target.id
            if not old_id or org_name in _EXPLICIT_NAME_ALIASES:
                data["organization_name"] = target.name
            sf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        repaired += 1

    return {
        "total": total,
        "already_valid": already_valid,
        "repaired": repaired,
        "skipped": skipped,
        "changes": changes,
    }


async def repair_pg_material_slots(
    database_url: str,
    orgs: List[Any],
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """修复 PG material_slots 中缺失于当前目录且不触发唯一键冲突的 subject_org_id。"""
    import asyncpg

    by_id = {o.id: o for o in orgs}
    idx = OrgNameIndex(orgs)

    conn = await asyncpg.connect(database_url, timeout=10)
    try:
        rows = await conn.fetch(
            """
            SELECT id, subject_org_id, subject_org_name, subject_kind, material_scope,
                   report_kind, fiscal_year, mapping_key, subject_level
            FROM material_slots
            """
        )
        existing_keys = {
            (
                r["subject_org_id"],
                r["subject_kind"],
                r["material_scope"],
                r["report_kind"],
                r["fiscal_year"] if r["fiscal_year"] is not None else -1,
                r["mapping_key"],
            )
            for r in rows
        }

        repaired = 0
        already_valid = 0
        collisions: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []

        for r in rows:
            old_id = str(r["subject_org_id"] or "").strip()
            if old_id in by_id:
                already_valid += 1
                continue

            preferred = r["subject_level"] if r["subject_level"] in ("department", "unit") else None
            if preferred is None and r["subject_kind"] in ("department", "unit"):
                preferred = r["subject_kind"]
            target, reason = resolve_target_org(
                r["subject_org_name"], orgs, idx, preferred_level=preferred
            )
            if target is None:
                skipped.append({
                    "slot_id": str(r["id"]),
                    "old_org_id": old_id,
                    "org_name": r["subject_org_name"],
                    "reason": reason,
                })
                continue

            new_key = (
                target.id,
                r["subject_kind"],
                r["material_scope"],
                r["report_kind"],
                r["fiscal_year"] if r["fiscal_year"] is not None else -1,
                r["mapping_key"],
            )
            if new_key in existing_keys:
                collisions.append({
                    "slot_id": str(r["id"]),
                    "old_org_id": old_id,
                    "target_org_id": target.id,
                    "org_name": r["subject_org_name"],
                    "fiscal_year": r["fiscal_year"],
                    "report_kind": r["report_kind"],
                })
                continue

            existing_keys.add(new_key)
            if apply:
                await conn.execute(
                    """
                    UPDATE material_slots
                    SET subject_org_id = $1,
                        subject_org_name = $2,
                        subject_level = CASE
                            WHEN subject_level IN ('unknown', '') OR subject_level IS NULL THEN $3
                            ELSE subject_level
                        END,
                        updated_at = NOW()
                    WHERE id = $4
                    """,
                    target.id,
                    target.name,
                    target.level,
                    r["id"],
                )
            repaired += 1

        return {
            "total": len(rows),
            "already_valid": already_valid,
            "repaired": repaired,
            "collisions": collisions,
            "skipped": skipped,
        }
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="修复历史任务与材料槽位的孤儿组织 ID")
    parser.add_argument("--apply", action="store_true", help="实际写入修改（默认只读 dry-run）")
    parser.add_argument("--skip-pg", action="store_true", help="跳过 PostgreSQL material_slots")
    args = parser.parse_args()

    store = OrganizationStorage()
    orgs = store.get_all()
    upload_summary = repair_upload_status_files(ROOT / "uploads", orgs, apply=args.apply)
    print(json.dumps({
        "mode": "apply" if args.apply else "dry_run",
        "uploads": {k: v for k, v in upload_summary.items() if k != "changes"},
    }, ensure_ascii=False, indent=2))

    if not args.skip_pg:
        from dotenv import dotenv_values
        cfg = dotenv_values(ROOT / ".env")
        db_url = cfg.get("DATABASE_URL")
        if db_url:
            pg_summary = asyncio.run(repair_pg_material_slots(db_url, orgs, apply=args.apply))
            print(json.dumps({"pg_material_slots": pg_summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
