"""Repair the verified local catalog mistake without changing material identity."""
import asyncio
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

load_dotenv('.env')
from src.db.connection import DatabaseConnection
from src.services.audit_log import append_audit_event
from src.services.org_storage import ORG_FILE, get_org_storage
from src.services.material_slot_resolver import decide_slot_allocation, to_org_record
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import ANALYSIS_DONE, REVIEW_REQUIRED

OUT = Path('output/system-validation-20261011').resolve()
ROOT_ID = '3034274efad9'


def write(name, payload):
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def main():
    receipt = json.loads(Path('output/system-validation-20261010/historical-ledger-recovery/receipt.json').read_text())
    store = get_org_storage()
    root = store.get_by_id(ROOT_ID)
    assert root.name == '上海市' and root.parent_id is None
    assert str(root.level) in ('department', 'city')
    original_level = str(root.level)
    records = [to_org_record(org) for org in store.get_all()]
    next(org for org in records if org['id'] == ROOT_ID)['level'] = 'city'
    prepared = []
    for item in receipt['items']:
        directory = Path('uploads') / item['job_id']
        status = json.loads((directory / 'status.json').read_text())
        decision = decide_slot_allocation(metadata=status, org_records=records)
        assert decision.ok and decision.jurisdiction_org_id == ROOT_ID
        assert status['status'] in ('review_required', 'done', 'completed')
        source = directory / status['filename']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item['source_sha256']
        prepared.append((item, status, decision))
    assert len(prepared) == 19
    conn = await DatabaseConnection.acquire()
    changed_catalog = False
    try:
        async with conn.transaction():
            before = []
            for item, status, decision in prepared:
                row = await conn.fetchrow('SELECT * FROM material_slots WHERE id=$1 FOR UPDATE', UUID(item['slot_id']))
                assert row and row['slot_key'] == decision.identity.slot_key
                assert str(row['current_document_version_id']) == str(item['document_version_id'])
                assert row['jurisdiction_org_id'] in (None, ROOT_ID)
                assert row['status'] in ('uploaded', 'review_required', 'reviewing', 'completed')
                before.append(dict(row))
            backup = OUT / 'catalog-repair-backup'
            backup.mkdir(exist_ok=True)
            if not (backup / 'organizations.json').exists():
                shutil.copy2(ORG_FILE, backup / 'organizations.json')
                (backup / 'material_slots.json').write_text(json.dumps(before, ensure_ascii=False, indent=2, default=str))
            if original_level != 'city':
                store.update(ROOT_ID, {'level': 'city'})
                changed_catalog = True
            service = MaterialSlotService(conn)
            repaired = []
            for (item, status, decision), old in zip(prepared, before):
                await conn.execute('UPDATE material_slots SET jurisdiction_org_id=$2, jurisdiction_name=$3, updated_at=NOW() WHERE id=$1',
                                   old['id'], ROOT_ID, decision.jurisdiction_name)
                if old['status'] in ('uploaded', 'review_required'):
                    await service.refresh_status(old['id'], analysis_state=ANALYSIS_DONE, review_state=REVIEW_REQUIRED)
                row = await conn.fetchrow('SELECT * FROM material_slots WHERE id=$1', old['id'])
                for key in ('slot_key', 'current_document_version_id', 'subject_org_id', 'report_kind', 'fiscal_year'):
                    assert row[key] == old[key]
                repaired.append({'slot_id': str(old['id']), 'job_id': item['job_id'], 'jurisdiction': row['jurisdiction_name'],
                                 'previous_status': old['status'], 'status': row['status'], 'identity_and_version_unchanged': True})
        result = {'at': datetime.now(timezone.utc).isoformat(), 'organization_id': ROOT_ID, 'previous_level': original_level,
                  'level': 'city', 'repaired': len(repaired), 'items': repaired}
        write('catalog-repair-receipt.json', result)
        append_audit_event(action='materials.local_catalog_repair', actor='system-maintenance', result='success',
                           resource_type='organization', resource_id=ROOT_ID, resource_name='上海市',
                           details={'previous_level': original_level, 'level': 'city', 'slots_repaired': len(repaired)})
        print(json.dumps({'repaired': len(repaired), 'identity_and_versions_preserved': True}, ensure_ascii=False))
    except Exception:
        if changed_catalog:
            store.update(ROOT_ID, {'level': original_level})
        raise
    finally:
        await DatabaseConnection.release(conn)
        await DatabaseConnection.close()


asyncio.run(main())
