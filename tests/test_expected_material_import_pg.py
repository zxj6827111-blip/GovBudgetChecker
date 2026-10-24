# ruff: noqa: F811
import asyncio

import pytest
from fastapi import HTTPException

from src.db.migrations import run_migrations
from src.services.expected_material_import import import_csv, preview_csv
from tests.remediation.test_round17_closure import RECORDS, GOOD, csv_text
from tests.test_material_slot_migration_pg import db, _conn, _seed_version  # noqa: F401, F811

pytestmark = pytest.mark.real_database


async def submit(conn, text, actor="reviewer"):
    preview = preview_csv(text, RECORDS, None)
    return await import_csv(
        conn,
        text=text,
        preview_sha256=preview["preview_sha256"],
        org_records=RECORDS,
        visible_org_ids=None,
        actor=actor,
    )


async def test_csv_atomic_replay_and_not_due_status(db):
    schema, pool = db
    await run_migrations()
    async with _conn(schema, pool) as conn:
        first = await submit(conn, csv_text(GOOD))
        second = await submit(conn, csv_text(GOOD))
        assert not first["replayed"] and second["replayed"]
        assert second["slots"] == first["slots"]
        assert first["slots"][0]["status"] == "not_due"
        assert first["slots"][0]["status_reason"] == "due_not_reached"
        assert await conn.fetchval("SELECT count(*) FROM material_slots") == 1
        assert await conn.fetchval("SELECT count(*) FROM expected_material_imports") == 1


async def test_csv_due_missing_and_same_name_department_unit_separate(db):
    schema, pool = db
    await run_migrations()
    old = GOOD.replace("2099-10-31", "2020-10-31")
    department = "p,department,2024,final,department_summary,summary,2099-10-31T23:59:59+08:00,"
    async with _conn(schema, pool) as conn:
        receipt = await submit(conn, csv_text(old, department))
        assert {row["status"] for row in receipt["slots"]} == {"not_due", "missing"}
        assert await conn.fetchval("SELECT count(DISTINCT slot_key) FROM material_slots") == 2
        assert (
            await conn.fetchval("SELECT count(DISTINCT subject_org_name) FROM material_slots") == 1
        )


async def test_csv_late_conflict_rolls_back_new_rows_and_receipt(db):
    schema, pool = db
    await run_migrations()
    async with _conn(schema, pool) as conn:
        await submit(conn, csv_text(GOOD))
        conflict = GOOD.replace("2099-10-31", "2098-10-31")
        other = GOOD.replace("2024", "2025")
        with pytest.raises(HTTPException) as exc:
            await submit(conn, csv_text(other, conflict))
        assert exc.value.status_code == 409
        assert await conn.fetchval("SELECT count(*) FROM material_slots") == 1
        assert await conn.fetchval("SELECT count(*) FROM expected_material_imports") == 1


async def test_csv_does_not_reset_completed_document(db):
    schema, pool = db
    await run_migrations()
    async with _conn(schema, pool) as conn:
        await submit(conn, csv_text(GOOD))
        version = await _seed_version(conn, prefix="csv-completed")
        await conn.execute(
            "UPDATE material_slots SET status='completed',status_reason='review_completed',current_document_version_id=$1",
            version,
        )
        # A distinct CSV payload reuses the same slot without changing completion.
        changed = GOOD.replace("https://example.gov.cn/source.pdf", "")
        await submit(conn, csv_text(changed))
        slot = await conn.fetchrow("SELECT * FROM material_slots")
        assert slot["status"] == "completed" and slot["current_document_version_id"] == version


async def test_concurrent_same_import_is_idempotent(db):
    schema, pool = db
    await run_migrations()

    async def one():
        async with _conn(schema, pool) as conn:
            return await submit(conn, csv_text(GOOD))

    receipts = await asyncio.gather(one(), one())
    assert sum(receipt["replayed"] for receipt in receipts) == 1
    async with _conn(schema, pool) as conn:
        assert await conn.fetchval("SELECT count(*) FROM material_slots") == 1
