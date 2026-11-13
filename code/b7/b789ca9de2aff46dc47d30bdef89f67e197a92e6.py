from pathlib import Path
import copy,json,os,sys
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT));os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='')
from tests.remediation.test_round11_ocr import fixture
from src.services.ocr_qualification import qualification
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_issues_payload
from tests.remediation.test_round8_percentage_binding import make_document
results={'scope':'synthetic fixture; actual qualification/promotion/materialisation functions; no blind metrics','cases':{}}
for name in ['baseline','rows_number_changed','cell_page_bbox_changed']:
 artifact,candidate=fixture()
 if name=='rows_number_changed':candidate.rows[1][2]='900'
 if name=='cell_page_bbox_changed':candidate.cells[6]['page_bbox']=[1.0,1.0,2.0,2.0]
 q=qualification(candidate,artifact,organization='测试局',year=2025,report_kind='final')
 promoted=promote_ocr_candidates(artifact,organization='测试局',year=2025,report_kind='final')
 context=build_fact_context_from_artifact(artifact,org_name='测试局',fiscal_year=2025,report_kind='final')
 facts=[{'fact_uid':f.fact_uid,'indicator':f.indicator,'raw_value':f.raw_value,'normalized_value':str(f.normalized_value),'recognition_status':f.recognition_status,'eligible':f in context.definite_facts(),'bbox':(f.extra_dims.get('evidence_refs') or [{}])[0].get('bbox'),'evidence':f.extra_dims.get('evidence_refs')} for f in context.facts]
 doc,rule,*_=make_document('income','本年收入合计100万元，财政拨款收入100万元，占100%。',fiscal=100)
 doc.page_tables[0][0][1][2]='100'
 doc.page_tables[0][0][1][-1]='0'
 doc.fact_context=context
 payload=build_issues_payload(doc,False,'final',[rule])
 results['cases'][name]={'production_rule_receipts':payload['fact_receipts'],'rule_findings':payload['issues']['all'],'qualification':q,'promotion':promoted,'candidate_rows':candidate.rows,'raw_cell_text':candidate.cells[6]['raw_text'],'cell_render_bbox':candidate.cells[6]['render_bbox'],'cell_page_bbox':candidate.cells[6]['page_bbox'],'facts':facts}
assert results['cases']['baseline']['qualification']['status']=='qualified'
assert results['cases']['rows_number_changed']['qualification']['status']=='rejected'
assert results['cases']['cell_page_bbox_changed']['qualification']['status']=='rejected'
assert not any(f['eligible'] for name,v in results['cases'].items() if name!='baseline' for f in v['facts'])
assert not any(str(f['raw_value']) in {'900','900.0'} for f in results['cases']['rows_number_changed']['facts'])
assert not any(f['bbox']==[1.,1.,2.,2.] or f['bbox']==(1.,1.,2.,2.) for f in results['cases']['cell_page_bbox_changed']['facts'])
(OUT/'ocr_qualification_probe_fixed.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps({name:{'qualified':v['qualification']['status'],'operands':[(f['indicator'],f['raw_value'],f['eligible'],f['bbox']) for f in v['facts']]} for name,v in results['cases'].items()},ensure_ascii=False,default=str))
