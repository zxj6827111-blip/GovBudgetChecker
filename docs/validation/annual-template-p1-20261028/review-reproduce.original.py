from pathlib import Path
from dataclasses import asdict
import asyncio
import json
import os
import sys
IMPLEMENTATION=Path('/Users/zhangxingjin/.codex/worktrees/annual-template-management/GovBudgetChecker')
sys.path.insert(0,str(IMPLEMENTATION))
from src.services.template_anchor_checks import additional_anchor_issues
from src.engine.checklist_text_rules import CMM008_TemplateResidue
from tests.test_material_writing_rules import document
from tests.test_template_anchor_checks import fixture_resolution
from src.schemas.parse_artifact import ParsedTextBlock,ParseArtifact
from src.services.material_writing import qualified_identity_view,collect,load
from src.services.template_scope import resolve_material_scope
from src.services.ocr_qualification import bound_ocr_block
from src.services.document_profile_resolver import resolve_document_profile
from src.services import annual_templates

results={}
# 使用经批准包的内存替身，仅测试来源门禁；不创建发布记录或业务决定。
pages=['上海市示例局2024年度决算','2024年度决算表\n说明：本表反映部门本年度收入。','2024年度决算情况说明']
os.environ.pop('DATABASE_URL',None)
os.environ.update(GBC_TEMPLATE_SCOPE='true',GBC_TEMPLATE_EXPORT='isolated-fixture',GBC_TEMPLATE_EXPORT_SHA256='isolated-fixture')
saved=annual_templates.offline_for_profile
annual_templates.offline_for_profile=lambda *a,**k:{'available':True,'capability_scope':{'writing_auto':True,'position_checks':True},'target_year':2024,'package_id':'isolated-qualified-fixture'}
rows=[]
try:
    for filename in ['示例局2024年度决算.pdf',None]:
        profile=resolve_document_profile(filename=filename,page_texts=pages)
        r=asyncio.run(load('isolated-readonly-probe','a'*64,profile,pages))
        rows.append({'filename':filename,'year_source':profile.report_year.source,'available':r['snapshot']['available'],'status':r['status']})
finally:annual_templates.offline_for_profile=saved
results['R1_filename_gate']={'reproduced':rows[0]['status']=='insufficient' and rows[1]['status']=='conflict','cases':rows}

anchor={'kind':'normative_section','scope':'unit','text':'2024年度依据《2024年办法》执行情况说明','annual_slot':True,'basis':'isolated audit fixture','source_id':'audit'}
text='2025年度依据《2024年办法》执行情况说明'
issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),document(['2025年度决算',text],fixture_resolution([anchor])),'normative_section')
results['R2_annual_slot']={'reproduced':bool(issues) and '《2025年办法》' in issues[0].message,'correct_heading':text,'issues':[i.message for i in issues],'pending':pending}

blocks=[ParsedTextBlock('orphan-cover',1,'2024年度部门决算',confidence=.99,extraction_method='ocr'),ParsedTextBlock('orphan-heading',2,'2024年度部门决算表',confidence=.99,extraction_method='ocr'),ParsedTextBlock('orphan-note',2,'本表反映部门本年度收入。',confidence=.99,extraction_method='ocr')]
artifact=ParseArtifact(source_file_hash='a'*64,page_count=2,text_blocks=blocks)
texts,spans=qualified_identity_view(['',''],artifact)
r=resolve_material_scope(evidence=collect(texts,source_spans=spans),template_qualified=True)
bound=[bound_ocr_block(b,artifact) for b in blocks]
results['R3_unbound_ocr']={'reproduced':not any(bound) and r.status=='confirmed','source_bound':bound,'accepted_ocr_blocks':len([s for s in spans if s['source_method']=='ocr']),'status':r.status,'value':r.value}

rows=[]
for kind,title,text,other in [('normative_section','新增政策执行情况说明','本报告尚未提供新增政策执行情况说明。',False),('normative_table','测试新增披露表','说明：测试新增披露表本年未编制。',True)]:
    anchor={'kind':kind,'scope':'all','text':title,'basis':'isolated audit fixture','source_id':'audit'}
    doc=document(['2025年度决算',text],fixture_resolution([anchor]))
    if other:doc.page_tables=[[],[[['其他项目','金额'],['其他事项','1.00']]]]
    issues,pending=additional_anchor_issues(CMM008_TemplateResidue(),doc,kind)
    rows.append({'kind':kind,'text':text,'unrelated_table_present':other,'issues':[i.message for i in issues],'pending':pending})
results['R4_structural_false_completion']={'reproduced':all(not r['issues'] and not r['pending'] for r in rows),'cases':rows}
assert all(v['reproduced'] for v in results.values())
print(json.dumps(results,ensure_ascii=False,indent=2))
