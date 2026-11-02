"""较早或较晚年度上传当年原件并核实无实质变化后可复用已有版本；没有当年原件时较晚版本只能作历史参考。"""
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from src.db.migrations import MIGRATIONS
from src.services import annual_templates as service

CAPS = {"position_checks": True}


@pytest.mark.asyncio
async def test_years_reuse_a_package_only_with_their_own_reviewed_original(real_database_url, tmp_path, monkeypatch):
    conn = await asyncpg.connect(real_database_url)
    schema = "annual_reuse_" + uuid4().hex
    monkeypatch.setenv("GBC_TEMPLATE_STORAGE", str(tmp_path / "storage"))
    await conn.execute(f'CREATE SCHEMA "{schema}"; SET search_path TO "{schema}"')
    try:
        for migration in MIGRATIONS:
            for sql in migration["sql"]:
                await conn.execute(sql)
        import fitz

        async def pair(year, unit_note="本表反映单位本年度收入"):
            ids = {}
            for kind, heading, note in (("unit", f"{year}年度决算表", unit_note),
                                        ("department", f"{year}年度部门决算表", "本表反映部门本年度收入")):
                path = tmp_path / (kind + uuid4().hex + ".pdf")
                pdf = fitz.open()
                page = pdf.new_page()
                page.insert_text((50, 50), heading + "\n" + note, fontname="china-s")
                pdf.save(path)
                pdf.close()
                ids[kind] = (await service.register_source(conn, path=path, metadata=dict(
                    report_kind="final", material_type=kind, source_report_year=year, issuer="test-only issuer",
                    release_reference=f"isolated-set-{year}", description="isolated test source", issue_date=""),
                    actor="test"))["id"]
            return ids

        async def reuse(ids, previous):
            draft = await service.create_draft(conn, dict(unit_source_id=ids["unit"], department_source_id=ids["department"],
                                                          previous_package_id=previous), "test")
            reviews = {side: ["report_year"] * len(changes) for side, changes in draft["data"]["changes"].items()}
            draft = await service.review(conn, draft["id"], 1, dict(basis="test-only slot review", visual_verified=True,
                                                                     decision="same", slot_reviews=reviews), "test")
            return draft, await service.publish(conn, draft["id"], 2, dict(version="ignored", capabilities=CAPS, validation={}), "test")

        base = await pair(2024)
        draft = await service.create_draft(conn, dict(unit_source_id=base["unit"], department_source_id=base["department"]), "test")
        await service.review(conn, draft["id"], 1, dict(basis="test-only visual review", visual_verified=True, decision="new"), "test")
        package = (await service.publish(conn, draft["id"], 2, dict(
            version="2024", capabilities=CAPS, validation={"position_checks": {"report_reference": "test-only"}}), "test"))["package_id"]
        bind = dict(region="上海市", overlay="", report_kind="final", package_id=package, expected_revision=0,
                    capabilities=CAPS, basis="isolated verification")

        # 较早年度：当年原件只差年度槽位，审核后复用 2024 版并继承已批准能力，不需要兼容样本。
        draft_2023, published = await reuse(await pair(2023), package)
        assert published["package_id"] == package
        await service.bind(conn, {**bind, "target_year": 2023, "adoption_kind": "verified_same",
                                  "source_draft_id": draft_2023["id"]}, "test")
        snap = await service.snapshot(conn, 2023)
        assert snap["available"] and snap["capability_scope"]["position_checks"] and not snap.get("compatibility_pending")
        # 较晚年度走同一条路径。
        draft_2025, _ = await reuse(await pair(2025), package)
        await service.bind(conn, {**bind, "target_year": 2025, "adoption_kind": "verified_same",
                                  "source_draft_id": draft_2025["id"]}, "test")
        assert (await service.snapshot(conn, 2025))["capability_scope"]["position_checks"]

        # 没有 2022 原件：不能借用 2023 的比较记录，也不能以正式下发或沿用冒充当年已生效。
        for adoption, extra in (("verified_same", {"source_draft_id": draft_2023["id"]}), ("verified_same", {}),
                                ("issued", {}), ("not_received", {})):
            with pytest.raises(HTTPException) as caught:
                await service.bind(conn, {**bind, "target_year": 2022, "adoption_kind": adoption, "capabilities": {}, **extra}, "test")
            assert caught.value.status_code == 422
        await service.bind(conn, {**bind, "target_year": 2022, "adoption_kind": "historical_reference", "capabilities": {}}, "test")
        assert (await service.snapshot(conn, 2022))["adoption_kind"] == "historical_reference"

        # 当年原件有实质变化时不能按“无变化”复用，须发布新版本。
        with pytest.raises(HTTPException):
            await reuse(await pair(2021, unit_note="本表反映单位本年度全部收入"), package)
        assert await conn.fetchval("SELECT count(*) FROM disclosure_template_packages") == 1
    finally:
        await conn.execute("SET search_path TO public")
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()
