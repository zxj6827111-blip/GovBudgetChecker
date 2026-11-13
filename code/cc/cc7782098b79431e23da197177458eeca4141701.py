"""清理修复复验中尚未发布的单位名称清洗错误占位；保留该失败版本供审计。"""
import asyncio,json,os,sys
from pathlib import Path
import asyncpg

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2];RT=OUT.parent/'runtime'
env=json.loads((RT/'environment.json').read_text());os.environ.update(env)
sys.path.insert(0,str(ROOT/'artifacts/pdf-ai-local-acceptance-20261026'))
from src.services.review_session_store import invalidate_stale_version_sessions
from src.services.material_slot_service import MaterialSlotService

async def main():
 c=await asyncpg.connect(env['DATABASE_URL'])
 try:
  async with c.transaction():
   repair=json.loads((OUT/'binding_repair_receipt.json').read_text())
   target=repair['unit_registration']['document_version_id']
   sid=await c.fetchval('SELECT slot_id FROM fiscal_document_versions WHERE id=$1',target)
   s=await c.fetchrow('SELECT * FROM material_slots WHERE id=$1 FOR UPDATE',sid)
   wrong=s['current_document_version_id'];assert wrong!=target
   w=await c.fetchrow('SELECT v.*,d.subject_org_id,o.org_name FROM fiscal_document_versions v JOIN fiscal_documents d ON d.id=v.document_id JOIN org_units o ON o.id=d.org_unit_id WHERE v.id=$1 FOR UPDATE OF v',wrong)
   t=await c.fetchrow('SELECT v.*,d.subject_org_id,o.org_name FROM fiscal_document_versions v JOIN fiscal_documents d ON d.id=v.document_id JOIN org_units o ON o.id=d.org_unit_id WHERE v.id=$1',target)
   assert w['subject_org_id']==t['subject_org_id']=='8c0ba1fafea0'
   assert w['file_hash']==t['file_hash'] and w['org_name']=='上海市信访办公室' and t['org_name']=='上海市信访办公室单位'
   assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM published_fact_projections WHERE document_version_id=$1)',wrong)
   assert await c.execute('UPDATE fiscal_document_versions SET slot_id=NULL WHERE id=$1 AND slot_id=$2',wrong,sid)=='UPDATE 1'
   assert await c.execute('UPDATE material_slots SET current_document_version_id=$2,updated_at=NOW() WHERE id=$1 AND current_document_version_id=$3',sid,target,wrong)=='UPDATE 1'
   await invalidate_stale_version_sessions(c,sid,reason='confirmed_directory_name_preserved')
   await MaterialSlotService(c).refresh_status(sid,analysis_state='not_started',review_state='none')
   receipt={'retained_failed_version_id':wrong,'correct_unit_version_id':target,'slot_id':str(sid),'originals_and_facts_preserved':True}
  (OUT/'intermediate_registration_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
  print(json.dumps(receipt,ensure_ascii=False))
 finally:await c.close()
asyncio.run(main())
