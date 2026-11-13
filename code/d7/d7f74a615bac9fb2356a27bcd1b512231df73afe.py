"""在隔离API/Worker/Postgres中运行10份新增原件，保留数据库和业务报告。"""
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('isolated_validation_new_batch', ROOT / 'scripts/run_isolated_service_validation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.database_receipt


async def capture(dsn, job_id, ingest=None, target_fact=None):
    result = await original(dsn, job_id, ingest, target_fact)
    if not ingest or not ingest.get('parse_run_id'):
        return result
    import asyncpg
    from src.services.parse_artifact_store import load_parse_artifact, load_parse_facts, load_parse_evidence
    conn = await asyncpg.connect(dsn)
    try:
        run_id = ingest['parse_run_id']
        artifact = await load_parse_artifact(conn, run_id)
        facts = await load_parse_facts(conn, run_id)
        evidence = await load_parse_evidence(conn, run_id)
        inventory = json.loads((BASE / 'inventory.json').read_text())
        doc = next((d for d in inventory if d['sha256'] == artifact.source_file_hash), None)
        if doc:
            out = BASE / 'service_database' / doc['doc_id']
            out.mkdir(parents=True, exist_ok=True)
            for name, data in [('artifact', artifact.to_dict()), ('facts', facts), ('evidence', evidence),
                               ('database_receipt', result), ('ingest', ingest)]:
                (out / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2))
    finally:
        await conn.close()
    return result


module.database_receipt = capture
inventory = json.loads((BASE / 'inventory.json').read_text())
inputs = BASE / 'service_inputs'
inputs.mkdir(exist_ok=True)
for doc in inventory[1:]:
    target = inputs / doc['filename']
    if not target.exists():
        target.symlink_to(BASE / 'documents' / doc['doc_id'] / 'source.pdf')
sys.argv = [str(ROOT / 'scripts/run_isolated_service_validation.py'), '--output', str(BASE / 'service'),
            '--final-pdf', str(BASE / 'documents' / inventory[0]['doc_id'] / 'source.pdf'),
            '--additional-pdfs-dir', str(inputs), '--timeout-seconds', '240']
raise SystemExit(module.main())
