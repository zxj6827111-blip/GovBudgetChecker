import asyncio, hashlib, json, os, sys
from pathlib import Path
import asyncpg

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RT=OUT.parent/'runtime'
env=json.loads((RT/'environment.json').read_text())
os.environ.update(env)
sys.path.insert(0,str(ROOT/'artifacts/pdf-ai-local-acceptance-20261026'))
from src.services.fact_compatibility_projection import archive_legacy_projection
from src.services.audit_log import append_audit_event

async def main():
 c=await asyncpg.connect(env['DATABASE_URL'])
 try:
  async with c.transaction():
   await c.fetchrow('SELECT id FROM fiscal_documents WHERE id=299 FOR UPDATE')
   head=await c.fetchrow('SELECT * FROM published_fact_projections WHERE document_id=299 FOR UPDATE')
   assert head and head['document_version_id']==300 and head['job_uuid']=='a296c928fbd69d0b173a5050667e3b6e'
   assert head['revision_id']=='rev-20261026T124826-f5caa922'
   old_version=await c.fetchrow('SELECT * FROM fiscal_document_versions WHERE id=300')
   assert old_version['slot_id'] is None
   repair=json.loads((OUT/'binding_repair_receipt.json').read_text())
   correct=await c.fetchrow('SELECT d.subject_org_id,v.file_hash,s.material_scope FROM fiscal_document_versions v JOIN fiscal_documents d ON d.id=v.document_id JOIN material_slots s ON s.id=v.slot_id WHERE v.id=$1',repair['unit_registration']['document_version_id'])
   assert correct['subject_org_id']=='8c0ba1fafea0' and correct['material_scope']=='unit_self' and correct['file_hash']==old_version['file_hash']
   archive_hash=await archive_legacy_projection(c,300,old_version['file_hash'])
   # 额外归档旧发布指针自身；旧事实、旧分析 revision 与旧版本全部保留。
   text=json.dumps({'retired_projection_head':dict(head),'reason':'user_confirmed_unit_was_misfiled_under_department','legacy_archive_hash':archive_hash},ensure_ascii=False,sort_keys=True,default=str)
   digest=hashlib.sha256(text.encode()).hexdigest()
   await c.execute('INSERT INTO legacy_projection_archives(document_version_id,payload_hash,payload_text) VALUES(300,$1,$2) ON CONFLICT DO NOTHING',digest,text)
   assert await c.fetchval('SELECT payload_text FROM legacy_projection_archives WHERE document_version_id=300 AND payload_hash=$1',digest)==text
   assert await c.execute('DELETE FROM published_fact_projections WHERE document_id=299 AND document_version_id=300 AND job_uuid=$1 AND revision_id=$2',head['job_uuid'],head['revision_id'])=='DELETE 1'
   receipt={'retired_live_head':dict(head),'legacy_archive_hash':archive_hash,'head_archive_hash':digest,'facts_and_history_preserved':True}
  (OUT/'retired_projection_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str))
  append_audit_event(action='retire_misfiled_projection_head',actor='Codex/user-authorized',result='success',resource_type='fiscal_document',resource_id='299',details={'old_version_id':300,'head_archive_hash':digest})
  print('Wrong live projection head archived and retired; snapshots, facts and analysis history retained')
 finally:await c.close()
asyncio.run(main())
