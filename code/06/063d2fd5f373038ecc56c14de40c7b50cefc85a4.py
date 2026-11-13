import asyncio,inspect,json,os,sys
sys.path.insert(0,sys.argv[1])
from src.services import material_writing,annual_templates
from src.schemas.document_profile import DocumentProfile,ProfileField
os.environ.pop('DATABASE_URL',None)
os.environ.update(GBC_TEMPLATE_SCOPE='true',GBC_BUDGET_TEMPLATE_SCOPE='true',GBC_TEMPLATE_EXPORT='synthetic-only')
profile=DocumentProfile(report_kind=ProfileField(value='budget',source='cover_title'),report_year=ProfileField(value=2026,source='cover_title'))
annual_templates.offline_for_profile=lambda *a: {'available':True,'capability_scope':{'writing_auto':True,'position_checks':True},'target_year':2026}
pages=['上海市测试局2026年度单位预算','2026年部门财务收支预算总表','2026年部门一般公共预算支出功能分类预算表']
kwargs={}
if 'archive_basis' in inspect.signature(material_writing.load).parameters:
 kwargs={'archive_basis':{'organization_id':'unit-test','organization_name':'上海市测试局','level':'unit','source':'upload_selection'},'cover_name':'上海市测试局'}
result=asyncio.run(material_writing.load('job-test','a'*64,profile,pages,'unit',**kwargs))
print(json.dumps({k:result.get(k) for k in ['status','source','value','reason','cover_independent_scope']},ensure_ascii=False))
