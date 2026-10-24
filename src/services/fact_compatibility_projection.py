"""旧查询表只投影已发布有效事实；不再从物理格重新判定金额与身份。"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import json

from src.services.analysis_fact_snapshot import canonical_facts, validate_snapshot


def snapshot_reference(snapshot):
    return {key: snapshot[key] for key in ('job_uuid', 'revision_id', 'snapshot_hash', 'effective_facts_hash')}


def snapshot_facts(snapshot):
    validate_snapshot(snapshot)
    return canonical_facts(snapshot['effective_context'].get('facts', []))


def snapshot_table_payloads(snapshot):
    """长表逐笔展示完整身份，避免同额、同科目不同阶段被旧行键合并。"""
    grouped = defaultdict(list)
    for fact in snapshot_facts(snapshot):
        grouped[fact['table_code'] or 'UNRESOLVED'].append(fact)
    result = []
    headers = ['科目编码', '科目名称', '指标', '阶段', '金额（万元）', '准入状态']
    for code, facts in sorted(grouped.items()):
        pages = sorted({ref['physical_page'] for f in facts
                        for ref in f['extra_dims'].get('evidence_refs', []) if ref.get('physical_page')})
        rows = [{'row_index': 0, 'cells': [{'col_index': i, 'raw_text': label, 'is_header': True}
                                         for i, label in enumerate(headers)]}]
        for index, fact in enumerate(facts, 1):
            fields = [fact['subject_code'], fact['subject_name'], fact['indicator'], fact['budget_stage'],
                      fact['normalized_value'], fact['recognition_status']]
            rows.append({'row_index': index, 'fact_uid': fact['fact_uid'],
                         'cells': [{'col_index': i, 'raw_text': value, 'is_header': False,
                                    'numeric_value': value if i == 4 else None}
                                   for i, value in enumerate(fields)]})
        result.append({'table_key': code, 'table_title': facts[0]['extra_dims'].get('source_table_title') or code,
                       'page_numbers': pages, 'row_count': len(rows), 'col_count': len(headers),
                       'data_json': {'source_system': 'GovBudgetChecker', 'table_key': code,
                                     'fact_projection': snapshot_reference(snapshot), 'rows': rows,
                                     'facts': facts}})
    return result


def snapshot_line_item_rows(snapshot):
    result = []
    for index, fact in enumerate(snapshot_facts(snapshot)):
        # 每条事实保留独立行。指标和阶段不同也不能覆盖 values_json 的同名键。
        path = fact.get('hierarchy_path') or []
        result.append({'table_key': fact['table_code'] or 'UNRESOLVED', 'row_index': index,
                       'item_name': fact['subject_name'] or '',
                       'class_code': path[0] if path else fact['subject_code'],
                       'type_code': path[1] if len(path) > 1 else None,
                       'item_code': path[2] if len(path) > 2 else None,
                       'values_json': {fact['indicator']: fact['normalized_value'],
                                      '_meta': {'fact_projection': snapshot_reference(snapshot),
                                                'recognition_status': fact['recognition_status'],
                                                'parse_confidence': fact['confidence']},
                                      '_facts': [fact]}})
    return result


async def archive_legacy_projection(conn, document_version_id, source_hash, *, shared_report_ids=None):
    """保留被替换的旧行和原 ID；金额由 PG 序列化，避免 Python 浮点改写历史。"""
    import uuid
    if shared_report_ids is None:
        head=await conn.fetchval('SELECT receipt FROM published_fact_projections WHERE document_version_id=$1',document_version_id)
        head=json.loads(head)if isinstance(head,str)else head
        shared_report_ids=[uuid.UUID(head['shared_report_id'])]if head else []
    payload = await conn.fetchval('''SELECT jsonb_build_object(
        'cells',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)
                 FROM fiscal_table_cells t WHERE document_version_id=$1),
        'instances',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)
                     FROM fiscal_table_instances t WHERE document_version_id=$1),
        'mappings',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)
                    FROM fiscal_column_mappings t JOIN fiscal_table_instances i ON i.id=t.table_instance_id
                    WHERE i.document_version_id=$1),
        'facts',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)
                 FROM fact_fiscal_line_items t WHERE document_version_id=$1),
        'reports',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)
                   FROM org_dept_annual_report t WHERE id=ANY($3::uuid[])),
        'shared_tables',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)
                         FROM org_dept_table_data t JOIN org_dept_annual_report r ON r.id=t.report_id WHERE r.id=ANY($3::uuid[])),
        'shared_lines',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)
                        FROM org_dept_line_items t JOIN org_dept_annual_report r ON r.id=t.report_id WHERE r.id=ANY($3::uuid[])),
        'source_file_hash',$2::text)::text''', document_version_id, source_hash, shared_report_ids)
    import hashlib
    digest = hashlib.sha256(payload.encode()).hexdigest()
    await conn.execute('INSERT INTO legacy_projection_archives(document_version_id,payload_hash,payload_text) '
                       'VALUES($1,$2,$3) ON CONFLICT DO NOTHING', document_version_id,digest,payload)
    stored = await conn.fetchval('SELECT payload_text FROM legacy_projection_archives '
                                 'WHERE document_version_id=$1 AND payload_hash=$2', document_version_id,digest)
    if stored != payload:
        raise ValueError('legacy projection archive readback differs from original rows')
    return digest


def effective_legacy_artifact(artifact, snapshot):
    """兼容原表保留标签和空白；有唯一事实的金额格采用当前有效值。"""
    from src.services.structured_ingest_runner import _legacy_artifact_projection
    projected, _ = _legacy_artifact_projection(deepcopy(artifact))
    by_source = defaultdict(list)
    for fact in snapshot_facts(snapshot):
        for ref in fact['extra_dims'].get('evidence_refs', []):
            if ref.get('source_cell_id'):
                by_source[ref['source_cell_id']].append(fact)
    from src.services.table_import_receipt import source_cell_id
    factors = {'元': Decimal('.0001'), '千元': Decimal('.1'), '万元': Decimal(1),
               '百万元': Decimal(100), '亿元': Decimal(10000)}
    for table in projected.table_fragments:
        for cell in table.cells:
            matches = by_source.get(source_cell_id(table, cell), [])
            if len(matches) != 1:
                continue
            fact = matches[0]
            if fact['normalized_value'] is None or fact['unit'] not in factors:
                continue
            value = Decimal(fact['normalized_value']) / factors[fact['unit']]
            from src.engine.fiscal_lexeme import amount_in_wan
            cell.raw_text = (fact['raw_value'] if amount_in_wan(fact['raw_value'],fact['unit']) == Decimal(fact['normalized_value'])
                             else format(value, 'f'))
            cell.numeric_value = float(value)  # 旧物理格列为浮点；规范金额仍在 NUMERIC 事实与快照中。
            cell.normalized_value_wan = Decimal(fact['normalized_value'])
            table.rows[cell.row_idx][cell.col_idx] = cell.raw_text
    return projected


async def verify_legacy_fact_projection(conn, document_version_id, snapshot):
    rows = await conn.fetch('SELECT amount,normalized_amount,table_code,statement_code,classification_type, '
                            'classification_code,classification_name,measure,parse_confidence,extra_dims, '
                            'instance_uid,row_order,classification_level,parent_classification_code,hierarchy_path '
                            'FROM fact_fiscal_line_items '
                            'WHERE document_version_id=$1', document_version_id)
    actual = []
    for row in rows:
        dims = json.loads(row['extra_dims']) if isinstance(row['extra_dims'], str) else row['extra_dims']
        fact = dims.get('snapshot_fact')
        if fact is None:
            raise ValueError('legacy fact projection is not bound to effective snapshot')
        for column, field in [('table_code', 'table_code'), ('classification_code', 'subject_code'),
                              ('classification_name', 'subject_name'), ('measure', 'indicator'),
                              ('parse_confidence', 'confidence'), ('instance_uid', 'instance_uid'),
                              ('classification_level', 'classification_level'),
                              ('parent_classification_code', 'parent_subject_code'), ('hierarchy_path', 'hierarchy_path')]:
            expected = (fact[field] or 'UNRESOLVED') if column == 'table_code' else fact[field]
            if row[column] != expected:
                raise ValueError('legacy fact projection differs: ' + column)
        value = Decimal(fact['normalized_value']) if fact['normalized_value'] is not None else None
        if (row['amount'] != value or row['normalized_amount'] != value
                or row['row_order'] != Decimal(fact['row_order'] or 0)
                or row['classification_type'] != (fact['extra_dims'].get('classification_type') or 'unresolved')
                or row['statement_code'] != (fact['extra_dims'].get('statement_code') or fact['table_code'] or 'UNRESOLVED')
                or dims.get('fact_projection') != snapshot_reference(snapshot)):
            raise ValueError('legacy fact projection amount or version differs from effective snapshot')
        actual.append(fact)
    if canonical_facts(actual) != snapshot_facts(snapshot):
        raise ValueError('legacy fact projection is incomplete or changed')


async def verify_shared_fact_projection(conn, report_id, snapshot):
    expected_tables = {p['table_key']: p['data_json'] for p in snapshot_table_payloads(snapshot)}
    actual_tables = await conn.fetch('SELECT table_key,data_json FROM org_dept_table_data WHERE report_id=$1', report_id)
    actual_tables = {r['table_key']: json.loads(r['data_json']) if isinstance(r['data_json'],str) else r['data_json']
                     for r in actual_tables}
    if actual_tables != expected_tables:
        raise ValueError('shared table projection differs from effective snapshot')
    expected_lines = {(p['table_key'],p['row_index']): p['values_json'] for p in snapshot_line_item_rows(snapshot)}
    actual_lines = await conn.fetch('SELECT table_key,row_index,values_json FROM org_dept_line_items WHERE report_id=$1', report_id)
    actual_lines = {(r['table_key'],r['row_index']): json.loads(r['values_json']) if isinstance(r['values_json'],str) else r['values_json']
                    for r in actual_lines}
    if actual_lines != expected_lines:
        raise ValueError('shared line projection differs from effective snapshot')
    reference = await conn.fetchval('SELECT fact_projection FROM org_dept_annual_report WHERE id=$1', report_id)
    reference = json.loads(reference) if isinstance(reference,str) else reference
    if reference != snapshot_reference(snapshot):
        raise ValueError('shared report projection version differs from effective snapshot')


async def publish_compatibility_projection(conn, snapshot, parse_run_id):
    """调用方持有发布事务；失败时旧事实、共享查询和当前指针一起回滚。"""
    facts = snapshot_facts(snapshot)
    document = await conn.fetchrow('''SELECT d.id AS document_id,v.id AS document_version_id,
        d.fiscal_year,d.doc_type,o.org_name,v.storage_key,v.original_filename,v.file_size_bytes
        FROM parse_runs r JOIN fiscal_document_versions v ON v.id=r.document_version_id
        JOIN fiscal_documents d ON d.id=v.document_id JOIN org_units o ON o.id=d.org_unit_id
        WHERE r.id=$1 FOR UPDATE OF d''', parse_run_id)
    if document is None:
        raise ValueError('published fiscal snapshot has no source document')
    previous = await conn.fetchrow('SELECT p.*,j.created_at AS job_created_at FROM published_fact_projections p '
                                   'JOIN analysis_jobs j ON j.job_uuid=p.job_uuid WHERE p.document_id=$1',
                                   document['document_id'])
    if previous and (previous['job_uuid'], previous['revision_id']) == (snapshot['job_uuid'], snapshot['revision_id']):
        if previous['snapshot_hash'] != snapshot['snapshot_hash']:
            raise ValueError('published compatibility projection version collision')
        await verify_legacy_fact_projection(conn, document['document_version_id'], snapshot)
        receipt = json.loads(previous['receipt']) if isinstance(previous['receipt'],str) else previous['receipt']
        import uuid
        await verify_shared_fact_projection(conn, uuid.UUID(receipt['shared_report_id']), snapshot)
        return
    if previous and previous['job_uuid'] != snapshot['job_uuid']:
        current_created = await conn.fetchval('SELECT created_at FROM analysis_jobs WHERE job_uuid=$1', snapshot['job_uuid'])
        if current_created is None or previous['job_created_at'] >= current_created:
            raise ValueError('older job cannot overwrite a newer document fact projection')

    from src.services.parse_artifact_store import load_parse_artifact
    from src.services.pdf_parser import PDFParser
    from src.services.table_recognizer import TableRecognizer
    artifact = await load_parse_artifact(conn, parse_run_id)
    from src.services.input_processing_identity import artifact_content_hash
    if (artifact.source_file_hash != snapshot['source_file_hash']
            or artifact.parse_run_uid != snapshot['parse_run_uid']
            or artifact_content_hash(artifact) != snapshot['artifact_content_hash']):
        raise ValueError('legacy projection artifact differs from published snapshot')
    await archive_legacy_projection(conn, document['document_version_id'],snapshot['source_file_hash'])
    projection = effective_legacy_artifact(artifact, snapshot)
    result = await PDFParser(conn).persist_artifact_to_legacy_tables(document['document_version_id'], projection)
    if not result['success']:
        raise ValueError('effective legacy table projection failed')
    recognizer = TableRecognizer(conn)
    instances = await recognizer.recognize_tables(document['document_version_id'])
    await recognizer.save_table_instances(document['document_version_id'], instances)
    await conn.execute('DELETE FROM fact_fiscal_line_items WHERE document_version_id=$1', document['document_version_id'])
    for fact in facts:
        dims = {'snapshot_fact': fact, 'fact_projection': snapshot_reference(snapshot)}
        refs = fact['extra_dims'].get('evidence_refs', [])
        await conn.execute('''INSERT INTO fact_fiscal_line_items
            (document_version_id,table_code,statement_code,classification_type,classification_code,
             classification_name,measure,amount,normalized_amount,extra_dims,row_order,
             classification_level,parent_classification_code,hierarchy_path,source_page_number,
             parse_confidence,instance_uid)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16)''',
            document['document_version_id'], fact['table_code'] or 'UNRESOLVED',
            fact['extra_dims'].get('statement_code') or fact['table_code'] or 'UNRESOLVED',
            fact['extra_dims'].get('classification_type') or 'unresolved', fact['subject_code'],
            fact['subject_name'], fact['indicator'],
            Decimal(fact['normalized_value']) if fact['normalized_value'] is not None else None,
            json.dumps(dims, ensure_ascii=False), Decimal(fact['row_order'] or 0),
            fact['classification_level'], fact['parent_subject_code'], fact['hierarchy_path'],
            refs[0].get('physical_page') if refs else None, fact['confidence'], fact['instance_uid'])
    await verify_legacy_fact_projection(conn, document['document_version_id'], snapshot)
    from pathlib import Path
    from src.services.ps_schema_sync import PSSharedSchemaSync
    shared = await PSSharedSchemaSync(conn).sync(
        document_version_id=document['document_version_id'], org_name=document['org_name'],
        fiscal_year=document['fiscal_year'], doc_type=document['doc_type'],
        pdf_path=Path(document['storage_key'] or document['original_filename'] or snapshot['job_uuid']),
        checksum=snapshot['source_file_hash'], fact_snapshot=snapshot,
        file_size_bytes=document['file_size_bytes'])
    if shared['status'] != 'done':
        raise ValueError('effective shared fact projection unavailable: ' + str(shared.get('reason')))
    receipt = {'fact_projection': snapshot_reference(snapshot), 'facts_count': len(facts),
               'shared_previous_archive_hash':shared.get('previous_archive_hash'),
               'shared_report_id': shared['report_id'], 'storage_consistent': True}
    await conn.execute('''INSERT INTO published_fact_projections
        (document_id,document_version_id,job_uuid,revision_id,snapshot_hash,effective_facts_hash,receipt)
        VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(document_id) DO UPDATE SET
        document_version_id=EXCLUDED.document_version_id,job_uuid=EXCLUDED.job_uuid,
        revision_id=EXCLUDED.revision_id,snapshot_hash=EXCLUDED.snapshot_hash,
        effective_facts_hash=EXCLUDED.effective_facts_hash,receipt=EXCLUDED.receipt,updated_at=NOW()''',
        document['document_id'],document['document_version_id'],snapshot['job_uuid'],snapshot['revision_id'],
        snapshot['snapshot_hash'],snapshot['effective_facts_hash'],json.dumps(receipt))
