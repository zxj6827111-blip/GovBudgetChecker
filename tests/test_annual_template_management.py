import asyncio
import json
from uuid import uuid4
from pathlib import Path
import pytest
import asyncpg
from fastapi import HTTPException
from src.services import annual_templates as service
from src.db.migrations import MIGRATIONS

@pytest.mark.asyncio
async def test_template_lifecycle_real_postgres(real_database_url, tmp_path, monkeypatch):
    # 只使用显式测试 DSN，并在独立 schema 执行完整迁移，不使用现有业务表。
    conn=await asyncpg.connect(real_database_url)
    schema='annual_test_'+uuid4().hex
    monkeypatch.setenv('GBC_TEMPLATE_STORAGE',str(tmp_path/'storage'))
    await conn.execute(f'CREATE SCHEMA "{schema}"; SET search_path TO "{schema}"')
    try:
        for migration in MIGRATIONS:
            for sql in migration['sql']: await conn.execute(sql)
        for migration in MIGRATIONS:
            if migration['id'].startswith(('2026-10-28_0042','2026-10-28_0043','2026-10-28_0044')):
                for sql in migration['sql']: await conn.execute(sql)
        import fitz
        async def imported(kind,content):
            path=tmp_path/(kind+uuid4().hex+'.pdf')
            pdf=fitz.open();p=pdf.new_page();p.insert_text((50,50),content,fontname='china-s');pdf.save(path);pdf.close()
            return await service.register_source(conn,path=path,metadata=dict(report_kind='final',material_type=kind,source_report_year=2024,
                issuer='test-only',release_reference='isolated-test-set',description='isolated test source',issue_date=''),actor='test')
        unit=await imported('unit','2024年度决算表\n本表反映单位本年度收入')
        dept=await imported('department','2024年度部门决算表\n本表反映部门本年度收入')
        draft=await service.create_draft(conn,dict(unit_source_id=unit['id'],department_source_id=dept['id']), 'test')
        with pytest.raises(HTTPException): await service.publish(conn,draft['id'],1,dict(version='v1',capabilities={},validation={}), 'test')
        draft=await service.review(conn,draft['id'],1,dict(basis='test-only visual review',visual_verified=True,decision='new'),'test')
        published=await service.publish(conn,draft['id'],2,dict(version='v1',capabilities={'position_checks':True},validation={'position_checks':{'report_reference':'test-only'}}),'test')
        package_id=published['package_id']
        bind=dict(region='上海市',overlay='',report_kind='final',target_year=2024,package_id=package_id,expected_revision=0,adoption_kind='issued',capabilities={'position_checks':True},basis='isolated verification')
        assert (await service.bind(conn,bind,'test'))['revision']==1
        snap=await service.snapshot(conn,2024);assert snap['available'] and snap['revision']==1
        with pytest.raises(HTTPException) as ex: await service.bind(conn,bind,'other')
        assert ex.value.status_code==409
        assert not (await service.snapshot(conn,2023))['available']
        # 相同来源只增加来源/沿用记录，不复制发布包。
        reuse=await service.create_draft(conn,dict(unit_source_id=unit['id'],department_source_id=dept['id'],previous_package_id=package_id),'test')
        reuse=await service.review(conn,reuse['id'],1,dict(basis='test-only identical source',visual_verified=True,decision='same'),'test')
        same=await service.publish(conn,reuse['id'],2,dict(version='ignored',capabilities={'position_checks':True},validation={}), 'test')
        assert same['package_id']==package_id
        assert await conn.fetchval('SELECT count(*) FROM disclosure_template_packages')==1
        await service.bind(conn,{**bind,'target_year':2025,'expected_revision':0,'adoption_kind':'not_received','capabilities':{}},'test')
        frozen=await service.freeze_current_bindings(conn)
        await service.bind(conn,{**bind,'expected_revision':1,'adoption_kind':'rollback','restore_revision':1},'test')
        from src.schemas.document_profile import DocumentProfile,ProfileField
        profile=DocumentProfile(report_kind=ProfileField(value='final'),report_year=ProfileField(value=2024))
        assert (await service.select_frozen(conn,frozen,profile,['上海市测试局2024年度决算']))['revision']==1
        assert (await service.snapshot(conn,2024))['revision']==2
        with pytest.raises(asyncpg.RaiseError): await conn.execute("UPDATE disclosure_template_packages SET content='{}'")
        # 并发写同一修订只能有一方成功。
        async def race():
            other=await asyncpg.connect(real_database_url)
            try:
                await other.execute(f'SET search_path TO "{schema}"')
                return await service.bind(other,{**bind,'expected_revision':2},'concurrent')
            except HTTPException as exc: return exc.status_code
            finally: await other.close()
        outcomes=await asyncio.gather(race(),race())
        assert sum(isinstance(o,dict) for o in outcomes)==1 and 409 in outcomes
        assert await conn.fetchval('SELECT count(*) FROM disclosure_template_audit')>=8
        bundle=await service.export_bundle(conn)
        import hashlib
        digest=hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert service.offline_snapshot(bundle,digest,2024)['available']
        assert not service.offline_snapshot(bundle,'bad',2024)['available']
        # 同时篡改文本及其自报哈希也不能绕过发布包中的不可变提取回执。
        directory=service.source_dir(unit['id']);manifest_path=directory/'manifest.json';text_path=directory/'extracted.txt'
        old_manifest=manifest_path.read_bytes();old_text=text_path.read_bytes()
        text_path.write_text('tampered text');manifest=json.loads(old_manifest);manifest['text_sha256']=hashlib.sha256(text_path.read_bytes()).hexdigest();manifest_path.write_text(json.dumps(manifest))
        assert not (await service.snapshot(conn,2024))['available']
        text_path.write_bytes(old_text);manifest_path.write_bytes(old_manifest)
        # 已发布原件被改写时能力 fail-closed，不能偷偷改选其他年份。
        directory=service.source_dir(unit['id']); original=directory/'original.pdf'; original.write_bytes(b'tampered')
        assert not (await service.snapshot(conn,2024))['available']
    finally:
        await conn.execute('SET search_path TO public')
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


def test_unverified_capability_cannot_be_enabled():
    with pytest.raises(HTTPException):service.validate_capabilities({'writing_auto':True},{'writing_auto':{'report_reference':'test-only'}})
    with pytest.raises(HTTPException):service.validate_capabilities({'arbitrary':True},{})
