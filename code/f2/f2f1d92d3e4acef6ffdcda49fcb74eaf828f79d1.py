"""隔离API/Worker/Postgres全10份入库，并注入真实v24旧缓存验证版本隔离。"""
import importlib.util
import json
from pathlib import Path
import sys
import uuid

BASE = Path(__file__).resolve().parent
RUN = BASE / (sys.argv[1] if len(sys.argv) > 1 else 'service_final')
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('isolated_v26', ROOT / 'scripts/run_isolated_service_validation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.database_receipt
seeded = {}
inventory = json.loads((BASE / 'baseline/inventory.json').read_text())


async def capture(dsn, job_id, ingest=None, target_fact=None):
    result = await original(dsn, job_id, ingest, target_fact)
    if not ingest or not ingest.get('parse_run_id'):
        return result
    import asyncpg
    from src.schemas.parse_artifact import ParseArtifact, FiscalFact
    from src.services.parse_artifact_store import (
        load_parse_artifact, load_parse_facts, load_parse_evidence,
        persist_parse_artifact, persist_facts_and_evidence, find_cached_parse_run,
    )
    conn = await asyncpg.connect(dsn)
    try:
        run_id = ingest['parse_run_id']
        artifact = await load_parse_artifact(conn, run_id)
        facts = await load_parse_facts(conn, run_id)
        evidence = await load_parse_evidence(conn, run_id)
        doc = next((d for d in inventory if d['sha256'] == artifact.source_file_hash), None)
        if doc:
            revision = (result.get('job') or {}).get('analysis_revision')
            out = RUN / 'service_database' / doc['doc_id'] / job_id / str(revision)
            out.mkdir(parents=True, exist_ok=True)
            for name, data in [('artifact', artifact.to_dict()), ('facts', facts), ('evidence', evidence),
                               ('database_receipt', result), ('ingest', ingest)]:
                (out / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2))
            version = ingest['document_version_id']
            if version not in seeded:
                folder = BASE / 'baseline/documents' / doc['doc_id']
                old = ParseArtifact.from_dict(json.loads((folder / 'artifact.json').read_text()))
                assert old.parser_fingerprint.startswith('pdfplumber-cells-v25-')
                old.config_fingerprint = artifact.config_fingerprint
                old.parse_run_uid = 'stale-v25-' + uuid.uuid4().hex
                old_id = await persist_parse_artifact(conn, old, document_version_id=version)
                oldfacts = [FiscalFact.from_dict(f) for f in json.loads((folder / 'facts.json').read_text())]
                await persist_facts_and_evidence(conn, parse_run_id=old_id, facts=oldfacts, file_hash=old.source_file_hash)
                keys = dict(document_version_id=version, source_file_hash=artifact.source_file_hash,
                            config_fingerprint=artifact.config_fingerprint, ocr_model_fingerprint=artifact.ocr_model_fingerprint)
                assert await find_cached_parse_run(conn, parser_fingerprint=old.parser_fingerprint, **keys) == old_id
                assert await find_cached_parse_run(conn, parser_fingerprint=artifact.parser_fingerprint, **keys) == run_id
                seeded[version] = dict(doc_id=doc['doc_id'], v25_run_id=old_id, v26_run_id=run_id,
                                       legacy_created_after_current=old_id > run_id, v26_lookup_rejected_newer_v25=True)
                (RUN / 'service_cache_isolation.json').write_text(json.dumps(list(seeded.values()), ensure_ascii=False, indent=2))
            rows = [dict(r) for r in await conn.fetch(
                'SELECT id,parser_fingerprint,config_fingerprint,status FROM parse_runs WHERE document_version_id=$1 ORDER BY id', version)]
            (out / 'cache_runs.json').write_text(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
    finally:
        await conn.close()
    return result


module.database_receipt = capture
inputs = BASE / 'service_inputs'
inputs.mkdir(exist_ok=True)
frozen = ROOT / 'outputs/shanghai_2024_new9_acceptance_20261016/documents'
for doc in inventory[1:]:
    target = inputs / doc['filename']
    if not target.exists():
        target.symlink_to(frozen / doc['doc_id'] / 'source.pdf')
sys.argv = [str(ROOT / 'scripts/run_isolated_service_validation.py'), '--output', str(RUN / 'service_complete'),
            '--final-pdf', str(frozen / inventory[0]['doc_id'] / 'source.pdf'),
            '--additional-pdfs-dir', str(inputs), '--timeout-seconds', '240',
            '--native-correction-pdf', str(frozen / 'SH2024N9_03/source.pdf')]
raise SystemExit(module.main())
