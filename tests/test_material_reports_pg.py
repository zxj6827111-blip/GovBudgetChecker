"""独立 schema 中验证登记、同名隔离、复核失效和真实聚合 SQL；默认不连库。"""
import hashlib
import json
import uuid
import asyncpg
import pytest

from src.db.migrations import MIGRATIONS
from src.db.connection import DatabaseConnection
from src.services import material_upload_registration as registration
from src.services.material_report_query import load_report_rows
from src.services.structured_ingest_runner import _ensure_document_version

pytestmark = pytest.mark.real_database


@pytest.fixture
async def report_db(real_database_url, monkeypatch):
    conn = await asyncpg.connect(real_database_url)
    schema = "material_reports_test_" + uuid.uuid4().hex[:12]
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}",public')
    try:
        for migration in MIGRATIONS:
            for sql in migration["sql"]:
                await conn.execute(sql)
        async def acquire(): return conn
        async def release(_): pass
        async def ready(): return True
        monkeypatch.setattr(DatabaseConnection, "acquire", acquire)
        monkeypatch.setattr(DatabaseConnection, "release", release)
        monkeypatch.setattr(registration, "ensure_structured_ingest_ready", ready)
        monkeypatch.setattr(registration, "load_org_records", lambda: [
            {"id":"city","name":"上海市","level":"city","parent_id":None},
            {"id":"org","name":"医保局","level":"department","parent_id":"city"},
            {"id":"other","name":"医保局","level":"department","parent_id":"city"},
        ])
        yield conn
    finally:
        await conn.execute('SET search_path TO public')
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


def metadata(path, org="org"):
    return {"organization_id":org,"organization_name":"医保局","report_year":2023,"fiscal_year":"2023",
        "report_kind":"final","doc_type":"dept_final","checksum":hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.asyncio
async def test_received_file_two_tasks_and_new_version_do_not_inherit_review(report_db, tmp_path):
    conn=report_db
    path=tmp_path/"医保局.pdf";path.write_bytes(b"first pdf")
    first=await registration.register_uploaded_material("first",path,metadata(path))
    version=first["document_version_id"];slot=uuid.UUID(first["material_slot"]["slot_id"])
    jobs=[]
    for name in ("early","current"):
        jobs.append(await conn.fetchval("INSERT INTO analysis_jobs(job_uuid,filename,status,analysis_revision,metadata) VALUES($1,'医保局.pdf','review_required',1,$2::jsonb) RETURNING id",name,json.dumps({"organization_id":"org","structured_ingest":{"document_version_id":version}})))
        await conn.execute("INSERT INTO analysis_results(job_id) VALUES($1)",jobs[-1])
    await conn.execute("""INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,
        status,started_by,completed_by,completed_at,review_result)
        VALUES($1,$2,'current','current:1','completed','reviewer','reviewer',NOW(),$3::jsonb)""",slot,version,json.dumps({"issue_counts":{"confirmed":6}}))
    items=await load_report_rows(conn,visible_org_ids=None,job_accessible=lambda _:True)
    assert len(items)==1 and items[0]["version_count"]==1 and items[0]["analysis_task_count"]==2
    assert items[0]["review"]["status"]=="completed" and items[0]["review"]["confirmed_count"]==6
    # 同文件重放登记必须复用版本且不能抹掉已复核状态。
    duplicate=await registration.register_uploaded_material("duplicate",path,metadata(path))
    assert duplicate["document_version_id"]==version
    assert await conn.fetchval("SELECT status FROM review_sessions WHERE slot_id=$1",slot)=="completed"
    path.write_bytes(b"new pdf")
    new=await registration.register_uploaded_material("new",path,metadata(path))
    assert new["document_version_id"]!=version
    items=await load_report_rows(conn,visible_org_ids=None,job_accessible=lambda _:True)
    assert items[0]["version_count"]==2
    assert items[0]["review"]["status"]=="waiting_analysis"
    assert items[0]["previous_review"]["document_version_id"]==version
    assert await conn.fetchval("SELECT status FROM review_sessions WHERE slot_id=$1",slot)=="invalidated"
    assert await conn.fetchval("SELECT status FROM material_slots WHERE id=$1",slot)=="uploaded"


@pytest.mark.asyncio
async def test_same_name_organizations_do_not_share_documents_or_versions(report_db,tmp_path):
    path=tmp_path/"医保局.pdf";path.write_bytes(b"same bytes")
    first=await registration.register_uploaded_material("a",path,metadata(path,"org"))
    second=await registration.register_uploaded_material("b",path,metadata(path,"other"))
    assert first["document_id"]!=second["document_id"]
    assert first["document_version_id"]!=second["document_version_id"]
    items=await load_report_rows(report_db,visible_org_ids=["org"],job_accessible=lambda _:True)
    assert len(items)==1 and items[0]["organization_id"]=="org"


@pytest.mark.asyncio
async def test_registration_failure_rolls_back_document_and_slot(report_db,tmp_path,monkeypatch):
    path=tmp_path/"医保局.pdf";path.write_bytes(b"rollback")
    from src.services.material_slot_service import MaterialSlotService
    async def fail(*args): raise RuntimeError("binding rejected")
    monkeypatch.setattr(MaterialSlotService,"bind_document_version",fail)
    with pytest.raises(RuntimeError):
        await registration.register_uploaded_material("broken",path,metadata(path))
    assert await report_db.fetchval("SELECT count(*) FROM fiscal_document_versions")==0
    assert await report_db.fetchval("SELECT count(*) FROM material_slots")==0


@pytest.mark.asyncio
async def test_same_subject_registration_replay_is_idempotent(report_db,tmp_path):
    path=tmp_path/"医保局.pdf";path.write_bytes(b"same upload")
    versions=[await registration.register_uploaded_material(str(i),path,metadata(path)) for i in range(3)]
    assert len({v["document_version_id"] for v in versions})==1
    assert await report_db.fetchval("SELECT count(*) FROM material_slots")==1


@pytest.mark.asyncio
async def test_concurrent_upload_connections_keep_one_version(report_db,tmp_path,real_database_url,monkeypatch):
    import asyncio
    import contextvars
    schema=await report_db.fetchval("SELECT current_schema()")
    second=await asyncpg.connect(real_database_url,server_settings={"search_path":f'"{schema}",public'})
    active=contextvars.ContextVar("report_test_connection")
    async def acquire():return active.get()
    monkeypatch.setattr(DatabaseConnection,"acquire",acquire)
    path=tmp_path/"医保局.pdf";path.write_bytes(b"concurrent upload")
    async def upload(conn,jid):
        active.set(conn)
        return await registration.register_uploaded_material(jid,path,metadata(path))
    try:
        results=await asyncio.gather(upload(report_db,"one"),upload(second,"two"))
        assert results[0]["document_version_id"]==results[1]["document_version_id"]
        assert await report_db.fetchval("SELECT count(*) FROM material_slots")==1
    finally:await second.close()


@pytest.mark.asyncio
async def test_confirmed_identity_repair_preserves_review_and_supports_rollback(report_db,tmp_path):
    from src.services.material_identity_repair import repair_material_identity,rollback_material_identity
    path=tmp_path/"医保局.pdf";path.write_bytes(b"original")
    registered=await registration.register_uploaded_material("reviewed",path,metadata(path))
    version=registered["document_version_id"];slot=registered["material_slot"]["slot_id"]
    meta={"organization_id":"org","organization_name":"医保局","structured_ingest":{"document_version_id":version}}
    job=await report_db.fetchval("INSERT INTO analysis_jobs(job_uuid,filename,status,analysis_revision,metadata) VALUES('reviewed','医保局.pdf','review_required',1,$1::jsonb) RETURNING id",json.dumps(meta))
    await report_db.execute("INSERT INTO analysis_results(job_id) VALUES($1)",job)
    await report_db.execute("""INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,
        status,started_by,completed_by,completed_at,review_result)
        VALUES($1,$2,'reviewed','reviewed:1','completed','admin','admin',NOW(),'{"issue_counts":{"confirmed":6}}')""",uuid.UUID(slot),version)
    uploads=tmp_path/"uploads";directory=uploads/"reviewed";directory.mkdir(parents=True)
    status=directory/"status.json";status.write_text(json.dumps({**meta,"status":"review_required"}))
    links=tmp_path/"links.json";links.write_text(json.dumps({"links":[{"job_id":"reviewed","org_id":"org"}]}))
    status_before=status.read_bytes();links_before=links.read_bytes()
    receipt=await repair_material_identity(report_db,old_id="org",new_id="canonical",slot_ids=[slot],
        catalog=[{"id":"city","name":"上海市","level":"city"},{"id":"canonical","name":"医保局","level":"department","parent_id":"city"}],
        upload_root=uploads,links_file=links,receipt_dir=tmp_path/"receipt",actor="user-confirmed")
    assert receipt["state"]=="committed" and receipt["frozen_before"]==receipt["frozen_after"]
    assert await report_db.fetchval("SELECT subject_org_id FROM material_slots WHERE id=$1",uuid.UUID(slot))=="canonical"
    assert json.loads(status.read_text())["organization_id"]=="canonical"
    items=await load_report_rows(report_db,visible_org_ids=["canonical"],job_accessible=lambda p:p["organization_id"]=="canonical")
    assert items[0]["review"]["confirmed_count"]==6
    await rollback_material_identity(report_db,tmp_path/"receipt/receipt.json")
    assert status.read_bytes()==status_before and links.read_bytes()==links_before
    assert await report_db.fetchval("SELECT subject_org_id FROM material_slots WHERE id=$1",uuid.UUID(slot))=="org"
