"""按用户确认纠正两个验收任务；保留原件、旧解析结果及复核历史。"""
import asyncio
import json
import os
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
WT = ROOT / 'artifacts/pdf-ai-local-acceptance-20261026'
RT = OUT.parent / 'runtime'
env = json.loads((RT / 'environment.json').read_text())
os.environ.update(env)
sys.path.insert(0, str(WT))
from api import runtime
from api.routes.upload import _inspect_document_preflight, _organization_binding_conflict
from src.db.connection import DatabaseConnection
from src.services.material_upload_registration import register_uploaded_material
from src.services.material_slot_service import MaterialSlotService
from src.services.review_session_store import invalidate_stale_version_sessions
from src.services.structured_ingest_runner import close_structured_ingest_resources
from src.services.audit_log import append_audit_event
from src.services.user_store import UserStore

UNIT_JOB = 'a296c928fbd69d0b173a5050667e3b6e'
DEPT_JOB = 'bdba38fab5eab35c2e2c03539ed7036c'
UNIT_ID = '8c0ba1fafea0'
DEPT_ID = '5e26afdec7b9'

async def main():
    assert (OUT / 'before/database.dump').stat().st_size > 1000000
    p = RT / 'uploads' / UNIT_JOB
    old = runtime.read_json_file(p / 'status.json')
    assert old['organization_id'] == DEPT_ID and old['analysis_revision'] == 'rev-20261026T124826-f5caa922'
    org = runtime.require_org_storage().get_by_id(UNIT_ID)
    assert org.level == 'unit' and org.parent_id == DEPT_ID
    pdf = runtime.find_first_pdf(p)
    preflight = _inspect_document_preflight(filename=pdf.name, pdf_path=pdf)
    (OUT / 'unit_preflight_before.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2))
    assert preflight['current'] is None
    assert _organization_binding_conflict(preflight, runtime.to_dict(org)) is None
    binding = runtime.set_job_organization(UNIT_JOB, UNIT_ID, match_type='manual', confidence=1.0)
    confirmation = {'source': 'user_confirmation', 'reason': '用户明确确认此件为单位决算，归属上海市信访办公室单位；原页未明确汇总口径，该维度继续待确认。',
                    'previous_organization_id': DEPT_ID, 'organization_id': UNIT_ID}
    metadata = runtime.read_json_file(p / 'status.json')
    registration = await register_uploaded_material(UNIT_JOB, pdf, metadata)
    assert registration['status'] == 'registered' and registration['document_version_id'] != 300
    runtime.merge_job_status(p, {'binding_confirmation': confirmation, 'material_registration': registration})
    conn = await DatabaseConnection.acquire()
    try:
        async with conn.transaction():
            slot_id = await conn.fetchval('SELECT slot_id FROM fiscal_document_versions WHERE id=299')
            slot = await conn.fetchrow('SELECT * FROM material_slots WHERE id=$1 FOR UPDATE', slot_id)
            assert slot['subject_org_id'] == DEPT_ID and slot['current_document_version_id'] == 300
            old_version = await conn.fetchrow('SELECT * FROM fiscal_document_versions WHERE id=300 FOR UPDATE')
            assert old_version['slot_id'] == slot_id and old_version['file_hash'] == old['checksum']
            assert str(old_version['storage_key']).startswith(UNIT_JOB + '/')
            new_slot = await conn.fetchrow('SELECT s.* FROM material_slots s JOIN fiscal_document_versions v ON v.slot_id=s.id WHERE v.id=$1', registration['document_version_id'])
            assert new_slot['subject_org_id'] == UNIT_ID and new_slot['material_scope'] == 'unit_self'
            # 旧错误归档版本留在库中作审计，只解除它对部门材料台账的占位；金额、原件、旧 revision 不改。
            assert await conn.execute('UPDATE fiscal_document_versions SET slot_id=NULL WHERE id=300 AND slot_id=$1', slot_id) == 'UPDATE 1'
            assert await conn.execute('UPDATE material_slots SET current_document_version_id=299, updated_at=NOW() WHERE id=$1 AND current_document_version_id=300', slot_id) == 'UPDATE 1'
            invalidated = await invalidate_stale_version_sessions(conn, slot_id, reason='organization_binding_corrected_by_user')
            await MaterialSlotService(conn).refresh_status(slot_id, analysis_state='not_started', review_state='none')
            receipt = {'unit_binding': binding, 'confirmation': confirmation, 'unit_registration': registration,
                       'department_current_version_id':299,'retained_misfiled_historical_version_id':300,
                       'old_wrong_slot_detached':True,'invalidated_old_review_sessions':invalidated,
                       'unit_slot_id':str(new_slot['id']),'department_slot_id':str(slot_id)}
    finally:
        await DatabaseConnection.release(conn)
    append_audit_event(action='repair_material_organization_binding', actor='Codex/user-authorized', result='success', resource_type='job', resource_id=UNIT_JOB, details=receipt)
    (OUT / 'binding_repair_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str))
    UserStore().update_user('codex_local_validator', is_active=True)
    await close_structured_ingest_resources()
    print(json.dumps({k:receipt[k] for k in ['department_current_version_id','retained_misfiled_historical_version_id','unit_slot_id','department_slot_id','invalidated_old_review_sessions']},ensure_ascii=False,default=str))

if __name__ == '__main__':
    asyncio.run(main())
