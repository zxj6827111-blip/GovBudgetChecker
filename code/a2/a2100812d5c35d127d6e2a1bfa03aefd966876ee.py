"""Recover intake after the local database outage, preserving analysis and PDFs."""
import asyncio
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env')
from api.runtime import write_json_file
from src.db.connection import DatabaseConnection
from src.services.analysis_result_store import persist_analysis_job_snapshot
from src.services.material_slot_service import MaterialSlotService
from src.services.structured_ingest_runner import (
    build_ingest_metadata, close_structured_ingest_resources, run_structured_ingest,
)

ROOT = Path('uploads').resolve()
OUT = Path('output/system-validation-20261010/historical-ledger-recovery').resolve()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    receipts = []
    try:
        for directory in sorted(ROOT.iterdir()):
            status_file = directory / 'status.json'
            if not status_file.is_file():
                continue
            original = json.loads(status_file.read_text())
            previous = original.get('structured_ingest') or {}
            if previous.get('status') != 'skipped' or previous.get('reason') != 'database_unavailable':
                continue
            pdf = directory / str(original.get('filename') or '')
            if not pdf.is_file() or pdf.suffix.lower() != '.pdf':
                continue
            checksum = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if original.get('checksum') and checksum != original['checksum']:
                raise RuntimeError('source checksum mismatch ' + directory.name)
            backup = OUT / directory.name
            backup.mkdir(exist_ok=True)
            for name in ('status.json', 'structured_ingest.json', 'persistence.json', 'fact_context.json'):
                if (directory / name).is_file():
                    shutil.copy2(directory / name, backup / name)
            metadata = build_ingest_metadata(**{
                key: original.get(key) for key in (
                    'organization_id', 'organization_name', 'fiscal_year', 'doc_type',
                    'report_year', 'report_kind', 'checksum', 'document_profile',
                )
            })
            metadata.update(filename=pdf.name, storage_key=pdf.relative_to(ROOT).as_posix(),
                            storage_backend=original.get('storage_backend') or 'local',
                            file_size_bytes=pdf.stat().st_size)
            ingest = await run_structured_ingest(directory.name, pdf, metadata)
            if ingest.get('status') not in ('done', 'warning') or not (ingest.get('material_slot') or {}).get('bound'):
                receipts.append({'job_id': directory.name, 'status': 'not_restored', 'ingest': ingest})
                print('not_restored', directory.name, flush=True)
                continue
            if hashlib.sha256(pdf.read_bytes()).hexdigest() != checksum:
                raise RuntimeError('source changed ' + directory.name)
            updated = {**original, 'structured_ingest': ingest, 'historical_ledger_recovery': {
                'at': datetime.now(timezone.utc).isoformat(), 'previous_ingest_status': previous,
                'source_sha256': checksum, 'analysis_recomputed': False,
            }}
            if not await persist_analysis_job_snapshot(updated, include_results=False):
                raise RuntimeError('job metadata persistence failed ' + directory.name)
            conn = await DatabaseConnection.acquire()
            try:
                await MaterialSlotService(conn).refresh_status(ingest['material_slot']['slot_id'])
            finally:
                await DatabaseConnection.release(conn)
            write_json_file(directory / 'structured_ingest.json', ingest)
            write_json_file(status_file, updated)
            receipts.append({
                'job_id': directory.name, 'status': 'restored',
                'document_version_id': ingest['document_version_id'],
                'slot_id': ingest['material_slot']['slot_id'], 'parse_run_id': ingest.get('parse_run_id'),
                'unified_facts_count': ingest.get('unified_facts_count'), 'source_sha256': checksum,
                'source_unchanged': True, 'analysis_result_unchanged': updated.get('result') == original.get('result'),
            })
            write_json_file(OUT / 'receipt.json', {'items': receipts})
            print('restored', directory.name, ingest.get('unified_facts_count'), flush=True)
    finally:
        await close_structured_ingest_resources()
    write_json_file(OUT / 'receipt.json', {
        'restored': sum(item['status'] == 'restored' for item in receipts),
        'total': len(receipts), 'items': receipts,
    })
    print('completed', len(receipts), flush=True)


asyncio.run(main())
