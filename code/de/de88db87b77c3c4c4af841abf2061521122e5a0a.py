import os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT));os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='')
from scripts.run_zc07_acceptance import validate_dataset_manifest,validate_e2e_evidence,_evaluate_exact_location
from scripts.evaluate_golden_corpus import _truth_group
ids=['DOC-001-income','DOC-001-expense']; groups=[_truth_group({'truth_id':i}) for i in ids]
labels=[{'annotation_id':x,'truth_id':x,'label':k,'truth_region':{'page':1,'bbox':[1,1,2,2]}} for x,k in [('D','defect'),('A','acceptable'),('M','manual_review')]]
r={'scope':'formal pure-function contract probes; no frozen input or accuracy measurement','raw_defect_ids':ids,'raw_distinct_groups':len(set(ids)),'metric_group_ids':groups,'metric_distinct_groups':len(set(groups)),'mixed_label_exact_denominator':_evaluate_exact_location([{'golden':{'labels':labels},'report':{'matched':[]}}],ROOT),'template_dataset':validate_dataset_manifest(ROOT/'docs/acceptance_templates/dataset.template.json',ROOT),'template_e2e':validate_e2e_evidence(ROOT/'docs/acceptance_templates/e2e.template.json',ROOT)}
assert r['raw_distinct_groups']==2 and r['metric_distinct_groups']==1
assert r['mixed_label_exact_denominator']['truth_total']==1
assert r['template_dataset']['status']=='incomplete' and r['template_e2e']['status']=='incomplete'
(OUT/'root_acceptance_contract_probe.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'truth_group_collision':True,'exact_denominator_defect_only':True,'template_status':[r['template_dataset']['status'],r['template_e2e']['status']]}))
