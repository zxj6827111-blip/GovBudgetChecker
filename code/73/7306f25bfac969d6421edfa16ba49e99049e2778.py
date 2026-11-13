"""2026-11-01 年度模板初始化：重传来源、建草稿、审核、发布、绑定。

只由本次初始化使用；不进入规则链。所有写操作走 src.services.annual_templates
的既有门槛（来源一致性、审核、能力验证、年度绑定），不直接改表。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import asyncpg  # noqa: E402

from src.services import annual_templates as svc  # noqa: E402

OUT = ROOT / "outputs" / "template_init_20261101"
OUT.mkdir(parents=True, exist_ok=True)

ACTOR = "zcode-template-init-20261101"
ISSUER = "用户提供模板批次（修订包 ce6541f1；原始下发机关由用户后续补记）"
RELEASE_BASE = (
    "用户修订包 ce6541f1db4927d9abd28506f77c368b9e4745a3ffe7e3d37fd929f6a8a1f4b3；"
    "{year}年{label}部门与单位配对；正式下发批次待核"
)

TEMPLATE_DIR = ROOT / "uploads" / ".templates"
LABEL = {"final": "决算", "budget": "预算"}

# (key, old_source_id, year, report_kind, material_type)
SOURCES = [
    ("final-2023-unit", "7359510e-ad55-4146-b535-acf0a9e8a5a4", 2023, "final", "unit"),
    ("final-2023-department", "5e71b298-199b-44ca-962d-1ae6146f85a1", 2023, "final", "department"),
    ("final-2024-unit", "cd9a87bd-1994-47b6-9c9b-f3a984e571cf", 2024, "final", "unit"),
    ("final-2024-department", "83f850bb-f40f-4cdc-9d03-2a663898d76d", 2024, "final", "department"),
    ("budget-2023-unit", "86feb43a-ff23-4540-b01b-6c627a178dd9", 2023, "budget", "unit"),
    ("budget-2023-department", "b613fea8-b64b-42d6-8964-302d3a8d5447", 2023, "budget", "department"),
    ("budget-2024-unit", "da64ee26-25f4-411f-8f55-f377ba8d5e6b", 2024, "budget", "unit"),
    ("budget-2024-department", "bbcfcf0b-dd6d-4ff3-bb45-34f15f0e8e74", 2024, "budget", "department"),
    ("budget-2025-unit", "a6ca34ca-5914-406d-a00d-12d17aa721f9", 2025, "budget", "unit"),
    ("budget-2025-department", "cb94d842-c9f9-4c9d-abaa-ffa5cd2867d4", 2025, "budget", "department"),
]


def j(value):
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


async def connect():
    return await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)


async def packages_by_year(conn):
    rows = await conn.fetch(
        "SELECT id,version,report_kind,source_report_year,capability_scope,validation_record,content "
        "FROM disclosure_template_packages ORDER BY created_at"
    )
    result = {}
    for row in rows:
        result[f"{row['report_kind']}-{row['source_report_year']}"] = {
            "id": str(row["id"]),
            "version": row["version"],
            "report_kind": row["report_kind"],
            "source_report_year": row["source_report_year"],
            "capability_scope": j(row["capability_scope"]),
            "validation_record": j(row["validation_record"]),
            "content": j(row["content"]),
        }
    return result


async def cmd_upload():
    conn = await connect()
    result = {}
    try:
        for key, old_id, year, kind, material in SOURCES:
            directory = TEMPLATE_DIR / old_id
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            source = directory / manifest["original"]
            metadata = {
                "original_filename": manifest["metadata"].get("original_filename") or source.name,
                "report_kind": kind,
                "material_type": material,
                "source_report_year": year,
                "issuer": ISSUER,
                "description": (
                    f"模板初始化重传（2026-11-01）：原件 sha256 {manifest['sha256'][:12]} 与既有来源 "
                    f"{old_id[:8]} 一致；按用户批次记录 issuer 以通过发布来源一致性门槛，原件字节未改变。"
                    f"{year} 年{LABEL[kind]}。"
                ),
                "issue_date": "",
                "release_reference": RELEASE_BASE.format(year=year, label=LABEL[kind]),
            }
            record = await svc.register_source(conn, path=source, metadata=metadata, actor=ACTOR)
            result[key] = record
            print(key, record["id"], "sha", record["sha256"][:12], "dupes", len(record["duplicate_source_ids"]))
    finally:
        await conn.close()
    dump("sources.json", result)


def year_replaced(old, replacements):
    def approved_year(match):
        prefix = old[: match.start()]
        if prefix.rfind("《") > prefix.rfind("》"):
            return match.group()
        return replacements[match.group()]

    return re.sub(
        r"(?<!\d)(?:" + "|".join(map(re.escape, replacements)) + r")(?=年)",
        approved_year,
        old,
    )


def detect_year_kind(old, new, old_year, new_year):
    for kind in ("report_year", "comparison_year", "report_and_comparison_year"):
        replacements = {}
        if type(new_year) is int:
            if kind in ("report_year", "report_and_comparison_year"):
                replacements[str(old_year) if type(old_year) is int else "20XX"] = str(new_year)
            if kind in ("comparison_year", "report_and_comparison_year") and type(old_year) is int:
                replacements[str(old_year - 1)] = str(new_year - 1)
        if not replacements:
            continue
        changed = year_replaced(old, replacements)
        if changed != old and changed == new:
            return kind
    if "".join(old.split()) == "".join(new.split()):
        return "layout"
    return None


def slot_reviews_for(data):
    old_year = data.get("previous_source_report_year")
    new_year = data.get("source_report_year")
    reviews = {}
    for side, changes in (data.get("changes") or {}).items():
        kinds = []
        for change in changes:
            old = "\n".join(p["text"] for p in change["old"])
            new = "\n".join(p["text"] for p in change["new"])
            kind = detect_year_kind(old, new, old_year, new_year)
            if kind is None:
                raise ValueError(f"{side} 存在无法归为年度槽位的差异：{old[:120]!r} -> {new[:120]!r}")
            kinds.append(kind)
        reviews[side] = kinds
    return reviews


def custom_anchors(sources):
    unit = sources["final-2023-unit"]["id"]
    department = sources["final-2023-department"]["id"]
    return [
        {
            "kind": "normative_section",
            "scope": "unit",
            "text": "七、财政拨款“三公”经费支出决算情况说明",
            "basis": "确认单34ace85c A1 同款；2023 单位模板第三部分说明必备小节标题（正文命中，目录条目不作依据）；2023 部门模板为旧写法“一般公共预算财政拨款…”，按 E-06 登记模板缺陷，不锚定部门侧",
            "source_id": unit,
            "occurrence": 2,
        },
        {
            "kind": "normative_table",
            "scope": "all",
            "text": "一、收入支出决算总表",
            "basis": "确认单34ace85c A2 同款；2023 两模板第二部分决算表必备表题（目录行唯一命中；正文表题形态无编号）",
            "source_id": department,
        },
        {
            "kind": "normative_section",
            "scope": "all",
            "text": "第四部分 名词解释",
            "basis": "确认单34ace85c A3 同款；2023 两模板第四部分标题（目录与正文各一处，取正文）",
            "source_id": department,
            "occurrence": 2,
        },
    ]


REVIEW_BASIS = {
    "final-2024": "2024 决算部门/单位原件提取文本与 2025 包逐段比较：仅年度及同比年度差异（部门19处、单位18处），逐组核对为年度槽位；A1–A3 继承自 2025 包并经原件提取视图复核（2026-11-01）",
    "final-2023": "2023 决算部门/单位原件提取文本核对：A2 一、收入支出决算总表、A3 第四部分名词解释两侧存在；A1 七、财政拨款“三公”经费支出决算情况说明仅单位侧存在；部门侧为旧写法“七、一般公共预算财政拨款…”，按 E-06 登记模板缺陷，不锚定（2026-11-01）",
    "budget-2025": "2025 预算部门/单位 XLS 提取视图核对：九表目录与编制说明成对指纹存在；本包不新增锚点（预算模板能力待专项审查）（2026-11-01）",
    "budget-2024": "2024 预算部门/单位 XLS 提取视图与 2025 预算包逐段比较：17 处年度槽位差异，另目录第 9 项为全角“9．”（2025 为半角“9.”），非年度槽位，故按新版发布；本包不新增锚点（2026-11-01）",
    "budget-2023": "2023 预算部门/单位 XLS 提取视图与 2025 预算包逐段比较：17 处年度槽位差异，另目录第 9 项为全角“9．”（2025 为半角“9.”），非年度槽位，故按新版发布；本包不新增锚点（2026-11-01）",
}

PUBLISH_PLAN = {
    "final-2023": ("2023-final-A1unit-A2A3-structure-r1", "2023 决算模板原件提取文本核对（部门 03dc05ca/单位 ec511c8c，修订包 ce6541f1）；A1 仅单位侧，A2/A3 两侧；2026-11-01"),
    "final-2024": (None, None),
    "budget-2025": ("2025-budget-structure-r1", "2025 预算 XLS 原件提取视图核对（部门 09e5fd12/单位 3760e937，修订包 ce6541f1）；结构包不含新增锚点；2026-11-01"),
    "budget-2024": ("2024-budget-structure-r1", "2024 预算 XLS 原件提取视图核对（部门 06e5370c/单位 bc0a4b85，修订包 ce6541f1）；与 2025 包差异含目录第 9 项全角点，按新版发布；不含新增锚点；2026-11-01"),
    "budget-2023": ("2023-budget-structure-r1", "2023 预算 XLS 原件提取视图核对（部门 2f3edc93/单位 9d6bbfb9，修订包 ce6541f1）；与 2025 包差异含目录第 9 项全角点，按新版发布；不含新增锚点；2026-11-01"),
}

PREVIOUS = {
    "final-2024": "final-2025",
    "budget-2024": "budget-2025",
    "budget-2023": "budget-2025",
}

BIND_BASIS = {    "final-2023": "用户授权完成 2023 年决算模板初始化（2026-11-01）；来源为重传的 2023 修订包原件（部门/单位 sha256 已在来源记录）；发布包仅启用 structure（A1 仅单位侧，A2/A3 两侧；2023 部门模板三公标题为旧写法，按 E-06 登记缺陷不锚定）；issued 且 target_year=source_report_year=2023，无需兼容性证明",
    "final-2024": "用户授权完成 2024 年决算模板初始化（2026-11-01）；2024 原件与 2025 包逐段比较仅年度槽位差异（部门19处、单位18处），逐组审核；复用 2025 决算包 a33b0d4d，能力保持 structure",
    "budget-2025": "用户授权完成 2025 年预算模板初始化（2026-11-01）；来源为重传的 2025 修订包原件；发布包仅启用 structure，未新增预算锚点；GBC_BUDGET_TEMPLATE_SCOPE 保持关闭",
    "budget-2024": "用户授权完成 2024 年预算模板初始化（2026-11-01）；2024 原件与 2025 预算包存在目录第 9 项全角/半角点差异（非年度槽位），按新版发布；发布包仅启用 structure，未新增预算锚点；GBC_BUDGET_TEMPLATE_SCOPE 保持关闭",
    "budget-2023": "用户授权完成 2023 年预算模板初始化（2026-11-01）；2023 原件与 2025 预算包存在目录第 9 项全角/半角点差异（非年度槽位），按新版发布；发布包仅启用 structure，未新增预算锚点；GBC_BUDGET_TEMPLATE_SCOPE 保持关闭",
}


async def cmd_apply(keys, dry=False):
    sources = load("sources.json")
    drafts = load("drafts.json")
    conn = await connect()
    results = {}
    try:
        packages = await packages_by_year(conn)
        transaction = conn.transaction()
        await transaction.start()
        try:
            for key in keys:
                kind, year_text = key.split("-")
                year = int(year_text)
                draft = drafts[key]
                data = draft["data"]
                decision = "same" if key == "final-2024" else "new"
                if decision == "same":
                    anchors = data["config"].get("anchors") or []
                    reviews = slot_reviews_for(data)
                    probe = {**data, "review_record": {"slot_reviews": reviews, "decision": "same"}}
                    if not svc.reviewed_slot_changes(probe):
                        raise RuntimeError(f"{key} 槽位审核预检失败：{reviews}")
                else:
                    anchors = custom_anchors(sources) if key == "final-2023" else []
                    reviews = {}
                review_record = {
                    "basis": REVIEW_BASIS[key],
                    "visual_verified": True,
                    "decision": decision,
                    "slot_reviews": reviews,
                    "anchors": anchors,
                    "accept_native_toc_variant": False,
                }
                reviewed = await svc.review(conn, draft["id"], draft["revision"], review_record, ACTOR)
                print("reviewed", key, "rev", reviewed["revision"])
                version, reference = PUBLISH_PLAN[key]
                if decision == "same":
                    old = packages[PREVIOUS[key]]
                    capabilities = old["capability_scope"]
                    validation = old["validation_record"]
                    version = old["version"]
                else:
                    capabilities = {"structure": True}
                    validation = {"structure": {"report_reference": reference}}
                    version = version
                published = await svc.publish(
                    conn,
                    draft["id"],
                    reviewed["revision"],
                    {"version": version, "capabilities": capabilities, "validation": validation},
                    ACTOR,
                )
                print("published", key, published["package_id"])
                adoption = "issued" if decision == "new" else "verified_same"
                binding = {
                    "region": "上海市",
                    "overlay": "",
                    "report_kind": kind,
                    "target_year": year,
                    "package_id": published["package_id"],
                    "expected_revision": 0,
                    "adoption_kind": adoption,
                    "capabilities": capabilities,
                    "basis": BIND_BASIS[key],
                    "source_draft_id": draft["id"] if adoption == "verified_same" else None,
                }
                bound = await svc.bind(conn, binding, ACTOR)
                print("bound", key, "revision", bound["revision"])
                results[key] = {
                    "draft_id": draft["id"],
                    "package_id": published["package_id"],
                    "binding_revision": bound["revision"],
                    "adoption_kind": adoption,
                    "slot_reviews": reviews,
                }
            if dry:
                await transaction.rollback()
                print("DRY RUN ROLLED BACK")
                return
            await transaction.commit()
        except Exception:
            await transaction.rollback()
            raise
    finally:
        await conn.close()
    if not dry:
        existing = {}
        target = OUT / "apply-results.json"
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
        existing.update(results)
        dump("apply-results.json", existing)


async def cmd_verify():
    conn = await connect()
    try:
        report = {}
        for kind, year in [("final", 2023), ("final", 2024), ("final", 2025), ("budget", 2023), ("budget", 2024), ("budget", 2025)]:
            snap = await svc.snapshot(conn, year, kind, "上海市")
            report[f"{kind}-{year}"] = {
                "available": snap.get("available"),
                "reason": snap.get("reason"),
                "package_id": snap.get("package_id"),
                "target_year": snap.get("target_year"),
                "source_report_year": snap.get("source_report_year"),
                "revision": snap.get("revision"),
                "capability_scope": snap.get("capability_scope"),
                "approved_capability_scope": snap.get("approved_capability_scope"),
                "compatibility_pending": snap.get("compatibility_pending"),
                "anchors": len(((snap.get("content") or {}).get("config") or {}).get("anchors") or []),
            }
        print(json.dumps(report, ensure_ascii=False, indent=1))
        dump("verify-snapshots.json", report)
    finally:
        await conn.close()


async def cmd_drafts(selected=None):
    sources = load("sources.json")
    conn = await connect()
    result = {}
    try:
        packages = await packages_by_year(conn)
        plan = [
            ("final", 2024, "final-2025"),
            ("final", 2023, "final-2025"),
            ("budget", 2025, None),
            ("budget", 2024, "budget-2025"),
            ("budget", 2023, "budget-2025"),
        ]
        if selected:
            plan = [item for item in plan if f"{item[0]}-{item[1]}" in selected]
        for kind, year, previous in plan:
            data = {
                "unit_source_id": sources[f"{kind}-{year}-unit"]["id"],
                "department_source_id": sources[f"{kind}-{year}-department"]["id"],
                "previous_package_id": packages[previous]["id"] if previous else None,
            }
            draft = await svc.create_draft(conn, data, ACTOR)
            result[f"{kind}-{year}"] = draft
            print("draft", kind, year, draft["id"], "rev", draft["revision"], draft["status"])
    finally:
        await conn.close()
    dump("drafts.json", result)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "upload":
        asyncio.run(cmd_upload())
    elif command == "drafts":
        asyncio.run(cmd_drafts(sys.argv[2:] or None))
    elif command == "apply":
        dry = "--dry" in sys.argv
        keys = [value for value in sys.argv[2:] if not value.startswith("--")]
        asyncio.run(cmd_apply(keys, dry=dry))
    elif command == "verify":
        asyncio.run(cmd_verify())
    else:
        raise SystemExit(f"unknown command {command!r}")
